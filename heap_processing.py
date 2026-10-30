import sys
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureSink,
    QgsField,
    QgsFields,
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputNumber,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterRasterLayer,
)
from qgis.PyQt.QtCore import QVariant

sys.path.insert(0, str(Path(__file__).resolve().parent))
from core.volume import compute_vol


# Read band 1 of a raster as a masked array (NoData and NaN masked).
def read_masked(path):

    ds = gdal.Open(path)
    if ds is None:
        raise QgsProcessingException(f'Cannot open raster: {path}')
    band = ds.GetRasterBand(1)
    arr = np.ma.masked_invalid(band.ReadAsArray().astype(np.float64))
    nodata = band.GetNoDataValue()
    if nodata is not None:
        arr = np.ma.masked_equal(arr,nodata)
    return arr, ds.GetGeoTransform(), ds.GetProjection(), (ds.RasterYSize, ds.RasterXSize)


# Burn one polygon onto the raster grid; True where a pixel centre is inside.
def rasterize(geom_wkt, geotransform, projection, shape):

    srs = osr.SpatialReference()
    srs.ImportFromWkt(projection)
    drv = ogr.GetDriverByName("MEM") or ogr.GetDriverByName("Memory")
    vds = drv.CreateDataSource("heap")
    layer = vds.CreateLayer("heap", srs=srs, geom_type=ogr.wkbMultiPolygon)
    feat = ogr.Feature(layer.GetLayerDefn())
    feat.SetGeometry(ogr.CreateGeometryFromWkt(geom_wkt))
    layer.CreateFeature(feat)

    rows, cols = shape
    target = gdal.GetDriverByName("MEM").Create("", cols, rows, 1, gdal.GDT_Byte)
    target.SetGeoTransform(geotransform)
    target.SetProjection(projection)
    gdal.RasterizeLayer(target, [1], layer, burn_values=[1])
    return target.GetRasterBand(1).ReadAsArray().astype(bool)

class HeapVolumeAlgorithm(QgsProcessingAlgorithm):
    DEM = "DEM"
    BASE = "BASE"
    HEAPS = "HEAPS"
    OUTPUT = "OUTPUT"

    def name(self):
        return "heapvolume"


    def displayName(self):
        return "Heap volume (cut / fill / net)"


    def group(self):
        return "Volume"


    def groupId(self):
        return "volume"


    def shortHelpString(self):
        return (
            "Computes cut, fill and net volume for each heap polygon.\n"
            "dz = DEM - Base DEM. Fill = sum of positive dz x pixel area (material above base); "
            "Cut = sum of negative dz x pixel area, reported positive; Net = Fill - Cut.\n"
            "Both DEMs must share one grid. Heaps are reprojected to the DEM CRS. "
            "A pixel counts if its centre lies inside the polygon. NoData pixels are skipped and counted."
        )

    def createInstance(self):
        return HeapVolumeAlgorithm()


    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.DEM, "DEM (current surface)"))
        self.addParameter(QgsProcessingParameterRasterLayer(self.BASE, "Base DEM"))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.HEAPS, "Heap polygon(s)", [QgsProcessing.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFeatureSink(self.OUTPUT, "Heap volumes"))
        for key, label in (("TOTAL_FILL", "Total fill (m³)"),
                           ("TOTAL_CUT", "Total cut (m³)"),
                           ("TOTAL_NET", "Total net (m³)")):
            self.addOutput(QgsProcessingOutputNumber(key, label))


    def processAlgorithm(self, parameters, context, feedback):
        dem_layer = self.parameterAsRasterLayer(parameters, self.DEM, context)
        base_layer = self.parameterAsRasterLayer(parameters, self.BASE, context)
        heaps = self.parameterAsSource(parameters, self.HEAPS, context)

        if dem_layer.crs() != base_layer.crs():
            raise QgsProcessingException(
                f"DEM CRS {dem_layer.crs().authid()} differs from base CRS {base_layer.crs().authid()}")
        if dem_layer.crs().isGeographic():
            raise QgsProcessingException("DEM must be in a projected CRS (metres) to compute volumes")

        dem, gt, proj, shape = read_masked(dem_layer.source())
        base, base_gt, _, base_shape = read_masked(base_layer.source())
        if shape != base_shape or not np.allclose(gt, base_gt):
            raise QgsProcessingException(
                f"DEM and base DEM are not on the same grid: shapes {shape} vs {base_shape}, "
                f"geotransforms {gt} vs {base_gt}. Align them first (e.g. Raster > Align Rasters).")
        pixel_area = abs(gt[1] * gt[5])
        feedback.pushInfo(f"Grid {shape[1]} x {shape[0]} px, pixel area {pixel_area:.6f} m²")

        fields = QgsFields()
        for name, kind in (("heap_id", QVariant.Int), ("area_m2", QVariant.Double),
                           ("fill_m3", QVariant.Double), ("cut_m3", QVariant.Double),
                           ("net_m3", QVariant.Double), ("valid_px", QVariant.Int),
                           ("nodata_px", QVariant.Int)):
            fields.append(QgsField(name, kind))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, heaps.wkbType(), dem_layer.crs())

        xform = QgsCoordinateTransform(heaps.sourceCrs(), dem_layer.crs(), context.transformContext())
        total = {"fill": 0.0, "cut": 0.0, "net": 0.0}
        count = heaps.featureCount() or 1

        for i, feat in enumerate(heaps.getFeatures()):
            if feedback.isCanceled():
                break
            geom = feat.geometry()
            geom.transform(xform)
            inside = rasterize(geom.asWkt(), gt, proj, shape)
            if not inside.any():
                feedback.reportError(f"Heap {feat.id()}: no pixel centre inside the DEM, skipped")
                continue

            r = compute_vol(base, dem, inside, pixel_area)
            if r.nodata_pixels:
                feedback.reportError(
                    f"Heap {feat.id()}: {r.nodata_pixels} NoData pixels skipped; volume may be underestimated")
            feedback.pushInfo(
                f"Heap {feat.id()}: fill {r.fill_m3:,.2f} m³ | cut {r.cut_m3:,.2f} m³ | net {r.net_m3:,.2f} m³")

            out = QgsFeature(fields)
            out.setGeometry(geom)
            out.setAttributes([feat.id(), geom.area(), r.fill_m3, r.cut_m3, r.net_m3,
                               r.valid_pixels, r.nodata_pixels])
            sink.addFeature(out, QgsFeatureSink.FastInsert)

            total["fill"] += r.fill_m3
            total["cut"] += r.cut_m3
            total["net"] += r.net_m3
            feedback.setProgress(int(100 * (i + 1) / count))

        if total["fill"] == 0 and total["cut"] > 0:
            feedback.reportError(
                "Every heap lies entirely below the base surface. "
                "Are the DEM and Base DEM inputs swapped?")

        feedback.pushInfo(f"TOTAL: fill {total['fill']:,.2f} | cut {total['cut']:,.2f} | net {total['net']:,.2f} m³")
        return {self.OUTPUT: dest_id, "TOTAL_FILL": total["fill"],
                "TOTAL_CUT": total["cut"], "TOTAL_NET": total["net"]}
