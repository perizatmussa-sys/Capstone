# Preliminary Machine Learning Experiment: Archaeological Site Prediction

## 1. Project Overview

This experiment is part of the capstone project **"Automated Site Detection, Location Prediction, and Heritage Risk Monitoring of Archaeological Sites Using Machine Learning."**

The main objective of this preliminary experiment was to explore how machine learning algorithms can be used to predict archaeological site presence based on environmental characteristics.

Before applying machine learning to archaeological sites associated with Karlag in Kazakhstan, a preliminary experiment was conducted using an existing archaeological dataset from Colombia.

The experiment focused on developing a machine learning workflow, training several classification models, and comparing their predictive performance.

## 2. Dataset

The experiment used a publicly available archaeological dataset from Colombia containing archaeological presence and pseudo-absence observations.

**Dataset source:** [Zenodo — How Modeling Frameworks Shape Archaeological Predictions](https://doi.org/10.5281/zenodo.23474116)

After preprocessing, a geographical subset containing **643 locations** was selected for the experiment.

The following environmental features were used:

- **Elevation:** Height of the terrain above sea level.
- **Slope:** Steepness of the terrain.

The target variable represented archaeological presence (1) or pseudo-absence (0).

## 3. Data Preprocessing

The following steps were performed:

1. Loaded and inspected the archaeological dataset.
2. Removed records with missing coordinates or target labels.
3. Removed duplicate geographical locations.
4. Selected a geographical subset of 643 observations.
5. Extracted elevation information from digital elevation data.
6. Calculated terrain slope.
7. Prepared the environmental features for machine learning.

The dataset was divided using a spatial train/test split to reduce the risk of evaluating models on geographically overlapping training and testing locations.

**Training set:** 372 locations

**Testing set:** 271 locations

## 4. Machine Learning Models

Five classification algorithms were trained and evaluated:

1. **Random Forest:** An ensemble algorithm combining multiple decision trees.
2. **Logistic Regression:** A statistical classification algorithm estimating the probability of archaeological presence.
3. **Decision Tree:** A model making predictions through a series of feature-based decisions.
4. **XGBoost:** A gradient
