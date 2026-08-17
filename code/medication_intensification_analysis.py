"""
Diabetes Medication Intensification and 30-Day Readmission Risk
Dataset: UCI Diabetes 130-US Hospitals (1999-2008)

Research question: Is inpatient insulin intensification (starting or
increasing insulin during the hospital stay) independently associated
with 30-day readmission risk, after adjusting for clinical severity --
or does it merely reflect that more severely ill patients both get
their insulin intensified AND are more likely to be readmitted
(confounding by indication)?

This is an association/adjustment study using interpretable logistic
regression (odds ratios), not a black-box prediction pipeline --
matching the methodological style used in claims-based HEOR work.
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import statsmodels.api as sm
import statsmodels.formula.api as smf

RAW_DATA_DIR = "./data/raw"
FIG_DIR = "./reports/figures"
REPORT_DIR = "./reports"

# ---------------------------------------------------------------
# 1. Load and clean (same base cleaning as the companion prediction project)
# ---------------------------------------------------------------
df = pd.read_csv(f"{RAW_DATA_DIR}/diabetic_data.csv")
print(f"Raw shape: {df.shape}")

df["readmitted_30"] = (df["readmitted"] == "<30").astype(int)
df = df.replace("?", np.nan)
df = df[df["race"].notna()]

death_hospice_codes = [11, 13, 14, 19, 20, 21]
df = df[~df["discharge_disposition_id"].isin(death_hospice_codes)]

# Restrict to encounters where the patient was actually on diabetes
# medication -- intensification is only a meaningful concept for
# patients who had a diabetes med regimen to begin with
df = df[df["diabetesMed"] == "Yes"].copy()
print(f"Diabetes-medicated cohort: {df.shape}")

# ---------------------------------------------------------------
# 2. Define the exposure: insulin intensification
# ---------------------------------------------------------------
# insulin: No / Down / Steady / Up
# "Up" = dose increased or insulin newly started during this admission
df["insulin_intensified"] = (df["insulin"] == "Up").astype(int)
df["insulin_decreased"] = (df["insulin"] == "Down").astype(int)
df["insulin_status"] = df["insulin"]

print("\nInsulin status distribution (diabetes-medicated cohort):")
print(df["insulin_status"].value_counts())
print(f"\n30-day readmission rate by insulin status:")
print(df.groupby("insulin_status")["readmitted_30"].mean().round(4))

# ---------------------------------------------------------------
# 3. Build adjustment covariates (confounders)
# ---------------------------------------------------------------
def age_midpoint(age_str):
    lo, hi = age_str.strip("[)").split("-")
    return (int(lo) + int(hi)) / 2

df["age_numeric"] = df["age"].apply(age_midpoint)
df["prior_utilization"] = (df["number_outpatient"] + df["number_emergency"]
                            + df["number_inpatient"])

# A1C testing/result as a marker of glycemic severity at admission
df["a1c_high"] = df["A1Cresult"].isin([">7", ">8"]).astype(int)
df["a1c_tested"] = df["A1Cresult"].notna().astype(int)

# Any medication change during the stay (broader marker of active
# management, separate from insulin specifically)
df["any_med_change"] = (df["change"] == "Ch").astype(int)

model_df = df.dropna(subset=["age_numeric", "number_diagnoses", "prior_utilization",
                               "time_in_hospital", "num_medications"]).copy()
print(f"\nAnalytic sample (complete cases): {len(model_df)}")

# ---------------------------------------------------------------
# 4. Unadjusted association
# ---------------------------------------------------------------
print("\n=== UNADJUSTED: 30-day readmission rate by insulin status ===")
unadj = model_df.groupby("insulin_status")["readmitted_30"].agg(["mean", "count"])
print(unadj)

# ---------------------------------------------------------------
# 5. Adjusted logistic regression
#    Outcome: 30-day readmission
#    Exposure: insulin intensified (Up) vs. Steady (reference)
#    Adjusting for: age, number of diagnoses (comorbidity proxy),
#    prior utilization, time in hospital, number of medications,
#    A1C severity marker
# ---------------------------------------------------------------
reg_df = model_df[model_df["insulin_status"].isin(["Steady", "Up", "Down", "No"])].copy()
reg_df["insulin_status"] = pd.Categorical(reg_df["insulin_status"],
                                            categories=["Steady", "Up", "Down", "No"])

formula = ("readmitted_30 ~ C(insulin_status, Treatment(reference='Steady')) "
           "+ age_numeric + number_diagnoses + prior_utilization "
           "+ time_in_hospital + num_medications + a1c_high")

model = smf.logit(formula, data=reg_df).fit(disp=0)
print("\n=== ADJUSTED LOGISTIC REGRESSION ===")
print(model.summary())

# Odds ratios with 95% CI
params = model.params
conf = model.conf_int()
conf.columns = ["2.5%", "97.5%"]
or_table = np.exp(pd.concat([params, conf], axis=1))
or_table.columns = ["OR", "OR_lower_95", "OR_upper_95"]
or_table["p_value"] = model.pvalues
print("\n=== ODDS RATIOS ===")
print(or_table.round(4))
or_table.to_csv(f"{REPORT_DIR}/odds_ratios.csv")

# ---------------------------------------------------------------
# 6. Secondary outcome: length of stay (cost proxy)
#    Does insulin intensification track with a longer stay --
#    i.e., is it a marker of a harder admission, not a "fix"?
# ---------------------------------------------------------------
los_formula = ("time_in_hospital ~ C(insulin_status, Treatment(reference='Steady')) "
               "+ age_numeric + number_diagnoses + prior_utilization + num_medications")
los_model = smf.ols(los_formula, data=reg_df).fit()
print("\n=== LENGTH OF STAY MODEL (secondary outcome) ===")
print(los_model.summary())

los_params = los_model.params
los_conf = los_model.conf_int()
los_conf.columns = ["2.5%", "97.5%"]
los_table = pd.concat([los_params, los_conf], axis=1)
los_table.columns = ["coef_days", "coef_lower_95", "coef_upper_95"]
los_table["p_value"] = los_model.pvalues
los_table.to_csv(f"{REPORT_DIR}/los_model.csv")

# ---------------------------------------------------------------
# 7. Plot: readmission rate by insulin status (unadjusted) +
#    forest-style plot of adjusted odds ratios
# ---------------------------------------------------------------
plt.figure(figsize=(7, 5))
order = ["No", "Down", "Steady", "Up"]
rates = model_df.groupby("insulin_status")["readmitted_30"].mean().reindex(order)
counts = model_df.groupby("insulin_status")["readmitted_30"].count().reindex(order)
bars = plt.bar(order, rates.values * 100, color=["#8FBFAE", "#5B9E8C", "#3E7C6B", "#D9A441"])
for i, (r, c) in enumerate(zip(rates.values, counts.values)):
    plt.text(i, r * 100 + 0.3, f"n={c:,}", ha="center", fontsize=9)
plt.ylabel("30-Day Readmission Rate (%)")
plt.title("Unadjusted 30-Day Readmission Rate by Insulin Status")
plt.tight_layout()
plt.savefig(f"{FIG_DIR}/readmission_by_insulin_status.png", dpi=150)
plt.close()

# Forest plot of adjusted ORs for insulin status categories
insulin_rows = [r for r in or_table.index if "insulin_status" in r]
labels = [r.split("[T.")[1].rstrip("]") for r in insulin_rows]
ors = or_table.loc[insulin_rows, "OR"]
lower = or_table.loc[insulin_rows, "OR_lower_95"]
upper = or_table.loc[insulin_rows, "OR_upper_95"]

plt.figure(figsize=(7, 4))
y_pos = range(len(labels))
plt.errorbar(ors, y_pos, xerr=[ors - lower, upper - ors], fmt="o", color="#3E7C6B",
             ecolor="#8FBFAE", capsize=4, markersize=8)
plt.axvline(1, color="k", linestyle="--", alpha=0.5)
plt.yticks(y_pos, [f"Insulin: {l}\n(vs. Steady)" for l in labels])
plt.xlabel("Adjusted Odds Ratio (95% CI)")
plt.title("Adjusted Association with 30-Day Readmission")
plt.tight_layout()
plt.savefig(f"{FIG_DIR}/adjusted_odds_ratios.png", dpi=150)
plt.close()

print("\nDone. Figures and reports written.")
