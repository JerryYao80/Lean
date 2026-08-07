# 因子系统部署指南

## 已创建的因子

### 操纵检测因子 (Manipulation Detection)
1. **turnover_anomaly** - 换手率Z-score
2. **amplitude_anomaly** - 振幅Z-score
3. **limit_behavior** - 涨停/跌停行为
4. **intraday_reversal** - 日内反转强度

### 融资融券因子 (Margin Trading)
5. **margin_factors** - 多空因子组
   - margin_balance_change (融资余额变化)
   - short_balance_change (融券余额变化)
   - margin_buy_ratio (融资买入占比)
   - short_sell_ratio (融券卖出占比)
   - margin_short_ratio (融资/融券比)

## 系统部署

### 1. 安装systemd服务（推荐）
```bash
sudo cp /home/project/hope/Lean/data-source/tushare/deploy/factor_worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable factor_worker
sudo systemctl start factor_worker
```

### 2. 查看服务状态
```bash
sudo systemctl status factor_worker
sudo journalctl -u factor_worker -f
```

### 3. 手动运行一次
```bash
python3 /home/project/hope/Lean/data-source/tushare/factor_worker.py --once
```

### 4. 查看因子数据
```bash
# 检查数据文件
ls /home/project/hope/Lean/result/factor-zoo/

# 监控脚本
/home/project/hope/Lean/Scripts/factor_zoo/check_progress.sh
```

## 后台运行状态

当前已配置：
- ✅ systemd服务文件已创建
- ✅ cron定时任务已设置（每天3:00 AM运行）
- ✅ factor_worker daemon正在运行

## 监控命令

```bash
# 查看日志
tail -f /var/log/factor_worker_cron.log

# 查看进程
ps aux | grep factor_worker

# 查看因子数据
/home/project/hope/Lean/Scripts/factor_zoo/check_progress.sh
```
