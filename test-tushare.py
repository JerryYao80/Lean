
import tushare as ts
token = "c735900235cd005d4a32c7fad8ef9bec4dbec1e4df8030d49f3c050a53bf"
pro = ts.pro_api(token)
pro._DataApi__token = token
pro._DataApi__http_url = 'http://106.54.191.157:5000'  
df = pro.daily(ts_code='000001.SZ', start_date='20180701', end_date='20180718')
print(df)
