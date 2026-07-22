在批准"任务20关闭"之前,有一个点我建议你再确认一下——这份数据里有个现象,报告把它解释为"DSR公式的预期行为",但我觉得更可能是设计层面的问题,而不是可以放行的信号。
核心疑点:200 trials,最优解从未被超越
best DSR 从 trial 0 到 trial 200 恒为 0.5,零漂移
后续 199 个 trial 的 mean DSR 仅 0.027,50.5% 甚至 ≤0.01
报告的解释:trial 0 时 n_trials=1,DSR 退化为 PSR(baseline=0),之后随 N 增大,expected_max 惩罚项上升,所以后面分数低"是公式预期行为"
问题在于:如果 Optuna 的 GP+EI 是拿这个"随 N 递增惩罚"的 DSR 当作每个 trial 的实时优化目标(reward),那这个目标函数本身就是非平稳的——同一组参数,在 trial 5 时算出来的 DSR 和在 trial 150 时算出来的 DSR 不一样(后者惩罚更重),纯粹是因为跑的 trial 数变多了,跟参数好坏无关。这直接违反了贝叶斯优化代理模型的基本假设(目标函数应该是参数的稳定函数),后果就是:
EI 采集函数会认为几乎所有后续参数组合都"不如 trial 0",因为门槛在不断抬高
优化器实质上从很早期就停止了有效探索,200 trials 里可能有 190+ 个从优化角度是"陪跑"的
"从未漂移"未必是搜索空间健康,更可能是搜索过程本身被卡死了——不是没有更好的参数,而是评分机制让更好的参数也显现不出来
Bailey & López de Prado 的 DSR 本来的用法:是在整轮搜索结束后,对最终选中的最优 Sharpe 做一次性的多重检验校正(用总 trial 数 N 去 deflate 这一个数),而不是嵌入到每个 trial 的实时 reward 里滚动计算。
建议核实的问题(比看 decay_ratio 更根本):
Optuna 的 objective_function 里,trial 内部实际拿去 trial.report() / 返回给 sampler 的,是不是就是这个随 N 变化的 DSR?检验脚本是docs/dsrdiag.py。
如果是——建议把每个 trial 的优化目标换成原始 Sharpe(或 Sortino/Calmar),DSR 只在跑完全部 trials 后,对 champion trial 做一次事后校正/门禁判断(比如"DSR > 0 才允许上线")
换目标后,重新跑一小批(比如 20-30 trials)看 best Sharpe 是否会随 trial 数增加而真正改善——如果之前一直卡在 trial 0,换掉惩罚项后应该能看到明显提升,这能直接证伪或证实我上面的猜测。

