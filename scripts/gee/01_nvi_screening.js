/**
 * EO-driven NVI Screening Workflow
 * ──────────────────────────────────────────────────────────────────────────
 * Metod: Remote sensing screening (pre-NVI) → identifiera potentiella
 * värdekärnor → stratifierad fältinventering
 *
 * Output (3 klasser):
 *   3 = Hotspot          → intensiv inventering
 *   2 = Mellanklass      → stickprov
 *   1 = Låg prioritet    → snabb verifiering
 *
 * Delindex (vikter):
 *   40% Strukturindex    – NDVI + textur + SAR backscatter
 *   40% Kontinuitetsindex – NDVI-stabilitet + ingen GFC-förlust
 *   20% Fuktindex        – NDMI + TWI (hydrologisk position)
 *
 * Testområde: Fiby urskog, Uppland, Sverige
 * Byt ut AOI-variabeln för att använda ett annat område.
 */

// ─── 1. AOI ─────────────────────────────────────────────────────────────────
// Standardvärde: Fiby urskog (~10 km²) – byt ut mot eget område
var AOI = ee.Geometry.Rectangle([17.02, 59.85, 17.12, 59.92]);
Map.centerObject(AOI, 13);
Map.addLayer(ee.Image(0).paint(AOI, 0, 2), {palette: 'red'}, 'AOI');

// ─── 2. Parametrar ───────────────────────────────────────────────────────────
var START_DATE     = '2022-05-01';
var END_DATE       = '2022-09-30';
var CLOUD_PCT_MAX  = 20;
var MIN_TREECOVER  = 20;   // % trädtäckning 2000 för att räknas som skog

// Vikter för sammansatt index (summa = 1.0)
var W_STRUCTURE   = 0.40;
var W_CONTINUITY  = 0.40;
var W_MOISTURE    = 0.20;

// ─── 3. Hjälpfunktioner ──────────────────────────────────────────────────────
function maskS2Clouds(img) {
  var qa  = img.select('QA60');
  var cloud  = 1 << 10;
  var cirrus = 1 << 11;
  var mask = qa.bitwiseAnd(cloud).eq(0).and(qa.bitwiseAnd(cirrus).eq(0));
  return img.updateMask(mask).divide(10000)
            .copyProperties(img, ['system:time_start']);
}

function addSpectralIndices(img) {
  var ndvi = img.normalizedDifference(['B8', 'B4']).rename('NDVI');
  var ndmi = img.normalizedDifference(['B8', 'B11']).rename('NDMI');
  return img.addBands([ndvi, ndmi]);
}

// ─── 4. Sentinel-2 SR ────────────────────────────────────────────────────────
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(AOI)
  .filterDate(START_DATE, END_DATE)
  .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', CLOUD_PCT_MAX))
  .map(maskS2Clouds)
  .map(addSpectralIndices);

print('Antal S2-scener:', s2.size());

var ndviMedian    = s2.select('NDVI').median().rename('ndvi_median');
var ndviStdDev    = s2.select('NDVI').reduce(ee.Reducer.stdDev()).rename('ndvi_std');
var ndmiMedian    = s2.select('NDMI').median().rename('ndmi_median');

// Lokal NDVI-heterogenitet (strukturkomplexitet inom 30 m)
var ndviTexture = ndviMedian.reduceNeighborhood({
  reducer: ee.Reducer.stdDev(),
  kernel:  ee.Kernel.square(30, 'meters')
}).rename('ndvi_texture');

// ─── 5. Sentinel-1 SAR (struktur & fukt) ─────────────────────────────────────
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(AOI)
  .filterDate(START_DATE, END_DATE)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
  .select(['VH', 'VV']);

var vhMedian = s1.select('VH').median().rename('vh_median');   // proxy för biomassa
var vvMedian = s1.select('VV').median().rename('vv_median');

// ─── 6. Hansen GFC 2023 (störningshistorik → kontinuitet) ────────────────────
var hansen = ee.Image('UMD/hansen/global_forest_change_2023_v1_11');

var treecover2000 = hansen.select('treecover2000');
var lossYear      = hansen.select('lossyear');

var isForest = treecover2000.gte(MIN_TREECOVER);
var hadLoss  = lossYear.gt(0).and(isForest);   // förlust registrerad

// 1 = obruten skogskontinuitet sedan 2000
var continuityScore = hadLoss.Not().multiply(isForest)
                              .rename('continuity_score');

// ─── 7. DEM & topografi (SRTM 30 m) ──────────────────────────────────────────
var dem   = ee.Image('USGS/SRTMGL1_003');
var slope = ee.Terrain.slope(dem);                        // grader

// Förenklat TWI: ln(flödesackumulering / tan(lutning))
// Flödesackumulering approximeras med invers lokal höjdskillnad
var slopeRad = slope.multiply(Math.PI / 180);
var tanSlope = slopeRad.tan().max(0.001);

var localMin = dem.reduceNeighborhood({
  reducer: ee.Reducer.min(),
  kernel:  ee.Kernel.square(5, 'pixels')
});
var drainageRelief = dem.subtract(localMin).max(1);
var twi = drainageRelief.log().multiply(-1)
            .subtract(tanSlope.log())
            .rename('twi');

// ─── 8. Sammansatta delindex ──────────────────────────────────────────────────

// 8.1 Strukturindex: NDVI-magnitud + lokal heterogenitet + SAR backscatter
//     Höga värden → hög biomassa, komplexitet, gammal skog
var structureIndex = ndviMedian.unitScale(0.3, 0.9)
  .add(ndviTexture.unitScale(0, 0.15))
  .add(vhMedian.unitScale(-22, -6))
  .divide(3)
  .rename('structure_index');

// 8.2 Kontinuitetsindex: NDVI-stabilitet + ingen GFC-förlust
//     Låg CV = stabil vegetation över säsonger = lång kontinuitet
var ndviCv       = ndviStdDev.divide(ndviMedian.abs().add(0.001));
var ndviStability = ndviCv.multiply(-1).unitScale(-0.4, 0);

var continuityIndex = ndviStability.multiply(0.6)
  .add(continuityScore.float().multiply(0.4))
  .rename('continuity_index');

// 8.3 Fuktindex: NDMI + TWI → proxy för alskogar, rikkärr, kantzoner
var moistureIndex = ndmiMedian.unitScale(-0.3, 0.5)
  .add(twi.unitScale(-8, 5))
  .divide(2)
  .rename('moisture_index');

// ─── 9. Sammansatt NVI-poäng ─────────────────────────────────────────────────
var nviScore = structureIndex.multiply(W_STRUCTURE)
  .add(continuityIndex.multiply(W_CONTINUITY))
  .add(moistureIndex.multiply(W_MOISTURE))
  .rename('nvi_score');

// ─── 10. Percentilbaserad hotspot-klassifikation ──────────────────────────────
var percentiles = nviScore.reduceRegion({
  reducer:  ee.Reducer.percentile([33, 67]),
  geometry: AOI,
  scale:    10,
  maxPixels: 1e9
});

var p33 = ee.Number(percentiles.get('nvi_score_p33'));
var p67 = ee.Number(percentiles.get('nvi_score_p67'));
print('Tröskel p33:', p33);
print('Tröskel p67:', p67);

var hotspotClass = ee.Image(3)
  .where(nviScore.lte(p67), 2)
  .where(nviScore.lte(p33), 1)
  .updateMask(nviScore.mask().and(isForest))
  .rename('hotspot_class');

// ─── 11. Areal per klass ─────────────────────────────────────────────────────
var classArea = hotspotClass.eq(ee.List([1, 2, 3]))
  .rename(['low', 'medium', 'hotspot'])
  .multiply(ee.Image.pixelArea().divide(10000))  // ha
  .reduceRegion({
    reducer:  ee.Reducer.sum(),
    geometry: AOI,
    scale:    10,
    maxPixels: 1e9
  });
print('Areal per klass (ha):', classArea);

// ─── 12. Visualisering ────────────────────────────────────────────────────────
Map.addLayer(
  ndviMedian.clip(AOI),
  {min: 0.3, max: 0.9, palette: ['#ffffcc', '#78c679', '#006837']},
  'NDVI median'
);
Map.addLayer(
  structureIndex.clip(AOI),
  {min: 0, max: 1, palette: ['white', '#1b7837']},
  'Strukturindex', false
);
Map.addLayer(
  continuityIndex.clip(AOI),
  {min: 0, max: 1, palette: ['white', '#2166ac']},
  'Kontinuitetsindex', false
);
Map.addLayer(
  moistureIndex.clip(AOI),
  {min: 0, max: 1, palette: ['white', '#35978f']},
  'Fuktindex', false
);
Map.addLayer(
  nviScore.clip(AOI),
  {min: 0, max: 1, palette: ['#313695', '#abd9e9', '#fee090', '#d73027']},
  'NVI-poäng (sammansatt)'
);
Map.addLayer(
  hotspotClass.clip(AOI),
  {min: 1, max: 3, palette: ['#4575b4', '#fee090', '#d73027']},
  'Hotspot-klassifikation'
);

// ─── 13. Export till Google Drive ─────────────────────────────────────────────
var exportBase = {region: AOI, scale: 10, crs: 'EPSG:32633', maxPixels: 1e9, folder: 'nvi_export'};

Export.image.toDrive(Object.assign({
  image: hotspotClass.byte(),
  description: 'NVI_Hotspot_Klass'
}, exportBase));

Export.image.toDrive(Object.assign({
  image: nviScore,
  description: 'NVI_Score'
}, exportBase));

Export.image.toDrive(Object.assign({
  image: ee.Image.cat([
    structureIndex.float(),
    continuityIndex.float(),
    moistureIndex.float(),
    ndviMedian.float(),
    ndviStdDev.float(),
    continuityScore.byte()
  ]),
  description: 'NVI_Delindex'
}, exportBase));
