import pyodbc

try:
    conn = pyodbc.connect(
        "DRIVER={ODBC Driver 18 for SQL Server};"
        "SERVER=103.175.163.77;"
        "DATABASE=GearUp;"
        "UID=1touch;"
        "PWD=tu[gx0K~?}n^I54yz!;"
        "TrustServerCertificate=yes;"
    )
    print("Connected")
    conn.close()
except Exception as e:
    print("Connection FAILED:")
    print(repr(e))