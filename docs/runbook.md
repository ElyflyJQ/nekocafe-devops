# Runbook

## 服务异常告警时

1. 在 Grafana 查看 P99/错误率
2. 在 Loki 检索最近 5min 的 ERROR 日志
3. 在 Tempo 找出涉事 traceId
