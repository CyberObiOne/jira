import pandas as pd

# change path to file
input_path = '1.csv'
output_path = "Transformed_AIO_CASE_Export_48.csv"

# 1. Download
df = pd.read_csv(input_path)

# 2.
df_filled = df.fillna(method='ffill')

# 3. Delete rows without data
df_cleaned = df_filled[df_filled["Steps"].notna()].copy()

# 4. Grouping data
def aggregate_case(group):
    return pd.Series({
        "S.NO.": group["S.NO."].iloc[0],
        "Key": group["Key"].iloc[0],
        "Title": group["Title"].iloc[0],
        "Description": group["Description"].iloc[0],
        "Pre-condition": group["Pre-condition"].iloc[0],
        "Datasets/Examples": group["Datasets/Examples"].iloc[0],
        "Steps": '\n'.join(f"{kw} {step}" for kw, step in zip(group["BDDKeyword"], group["Steps"])),
        "Data": group["Data"].iloc[0],
        "Expected Result": group["Expected Result"].iloc[0],
        "Folder": group["Folder"].iloc[0],
        "Requirements": group["Requirements"].iloc[0],
        "Owner": "Galyna Galynska",
        "Type": group["Type"].iloc[0],
        "Tags": group["Tags"].iloc[0],
        "Automation Status": "Cucumber",
        "Automation Key": group["Automation Key"].iloc[0],
        "Screenshot": group["Screenshot"].iloc[0]
    })

df_grouped = df_cleaned.groupby("Key").apply(aggregate_case).reset_index(drop=True)
df_grouped["S.NO."] = range(1, len(df_grouped) + 1)

# 5. Saving
df_grouped.to_csv(output_path, index=False, encoding="utf-8-sig")  # saved with Kirilic format

print(f"File saved: {output_path}")
