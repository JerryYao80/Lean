import tushare as ts
token = "c735900235cd005d4a32c7fad8ef9bec4dbec1e4df8030d49f3c050a53bf"
pro = ts.pro_api(token)
pro._DataApi__token = token
pro._DataApi__http_url = 'http://106.54.191.157:5000' 
df = pro.rt_k(ts_code='3*.SZ,6*.SH,0*.SZ,9*.BJ')
#df = pro.rt_etf_k(ts_code='518880.SZ')
print(df)
