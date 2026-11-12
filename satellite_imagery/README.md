
# Karlag Satellite Imagery Dataset

## Overview

This directory contains satellite imagery preprocessing scripts and dataset outputs for the Karlag archaeological remote sensing project.

The initial archaeological database contains:
- 60 known archaeological sites (S60)
- 129 recorded archaeological features (F129)

## Satellite Imagery

Source: Copernicus Sentinel-2 Level-2A

Imagery: True Color Image (TCI), RGB

Spatial resolution: 10 meters per pixel

Site image dimensions: 1000 x 1000 pixels

Ground coverage: 10 x 10 km per site

## Processing Workflow

1. Read archaeological site coordinates.
2. Identify Sentinel-2 imagery covering each site.
3. Download the required true-color JP2 files.
4. Transform geographic coordinates.
5. Crop a 10 x 10 km image around each site.
6. Export GeoTIFF and PNG images.
7. Overlay known archaeological feature coordinates.
8. Validate the generated dataset.

## Python Libraries

- Rasterio
- PyProj
- NumPy
- Pillow
- OpenPyXL

## Results

- 60 site-centered satellite images
- 60 GeoTIFF outputs
- 60 PNG previews
- 60 annotated PNG images

Automated technical validation:
- PASS: 60
- WARNING: 0
- FAIL: 0

## Limitations

Sentinel-2 RGB imagery has a spatial resolution of 10 m/pixel, which limits the visibility of small archaeological structures.

The feature annotations represent known geographic coordinates from the archaeological database, not automated ML detections.

## Future Work

- Evaluate higher-resolution imagery
- Review archaeological feature visibility
- Prepare verified machine-learning annotations
- Develop and evaluate archaeological detection models
