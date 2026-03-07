import csv

# Read the symbol properties database
with open('Data/symbol-properties/symbol-properties-database.csv', 'r') as f:
    reader = csv.reader(f)
    for row in reader:
        if row and (row[0] == 'sse' or row[0] == 'szse'):
            print(f"Market: {row[0]}, Symbol: {row[1]}, Type: {row[2]}, PriceMagnifier: {row[10] if len(row) > 10 else 'N/A'}")
