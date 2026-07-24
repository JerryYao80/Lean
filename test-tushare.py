
import tushare as ts
token = "f6a85318fac091a847bed0c7d5069b6679bccc0abc9346864bfa4b046a0d"
pro = ts.pro_api(token)
pro._DataApi__token = token
pro._DataApi__http_url = 'http://jiaoch.site'  
df = pro.daily(ts_code='000001.SZ', start_date='20180701', end_date='20180718')
print(df)
