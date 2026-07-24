import tushare as ts
# token秘钥（把给咱们的token复制过来哈）
token = "cdedc155a8f651348e78323b141973e7d07abc4eaeb2aec1d24311392cb6"
pro = ts.pro_api(token)
pro._DataApi__token = token  # 保证有这个代码，不然不可以获取
pro._DataApi__http_url = 'http://106.54.191.157:5000'  # 保证有这个代码，不然不可以获取
# 测试接口(换成自己的接口）
print(token)
# df = pro.daily(ts_code='000001.SZ', start_date='20180701', end_date='20180718')
# df = pro.stk_mins(ts_code='600000.SH', freq='1min', start_date='2023-08-25 09:00:00', end_date='2023-08-25 19:00:00')
df = pro.ft_mins(ts_code='CU2310.SHF', freq='1min', start_date='2023-08-25 09:00:00', end_date='2023-08-25 19:00:00')
print(df)