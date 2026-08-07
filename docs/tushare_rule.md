import tushare as ts

# 把 token 设为管理员给您的 API token
ts.set_token("eFyJJb1kIfjHor30N0zGk311M3jiUVs4I0nAAUiIO3jaEosJI9muUXifOBjCEz3TOlD0QMz3McDPM35VOdTqlC9")

# 修改 API 地址
pro = ts.pro_api()
pro._DataApi__http_url = "https://fastapic.stockai888.top"

# 然后正常用就行
df = pro.daily(ts_code='000001.SZ', start_date='20260101', end_date='20260110')
print(df)

有一特殊设置情形：像ts.pro_bar() 这类模块级函数，必须手动传 api=pro
import tushare as ts

# 把 token 设为管理员给您的 API token
ts.set_token("eFyJJb1kIfjHor30N0zGk311M3jiUVs4I0nAAUiIO3jaEosJI9muUXifOBjCEz3TOlD0QMz3McDPM35VOdTqlC9")

# 修改 API 地址
pro = ts.pro_api()
pro._DataApi__http_url = "https://fastapic.stockai888.top"

# 注意有些接口要独立传参数 api = pro
df = ts.pro_bar(ts_code='002594.SZ', api=pro , start_date='20180101', end_date='20181011', adj='qfq')
print(df)

