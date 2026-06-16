# NekoCafé

> NekoCafé 智慧餐饮预约平台 — 实验三 DevOps CI/CD PoC 仓库

## 项目简介

本仓库实现了 NekoCafé 平台的**预约服务（Reservation）**与**会员服务（Member）**两个核心微服务的容器化部署与 CI/CD 流水线。

## 前置条件

| 工具 | 最低版本 | 用途 |
|------|---------|------|
| Docker Desktop | 24.0+ | 容器运行时 |
| Docker Compose | 2.20+ | 本地多服务编排 |
| Node.js | 20 LTS | 会员服务本地开发 |
| Python | 3.12+ | 预约服务本地开发 |
| Helm | 3.12+ | K8s 部署 |
| kubectl | 1.28+ | K8s 集群操作 |
| Trivy | 0.45+ | 容器镜像安全扫描 |

## 一键启动

```bash
# 启动全部服务（含 PostgreSQL + Redis）
make up

# 等价于
docker compose up -d --build
```

## 验证

```bash
# 预约服务健康检查
curl http://localhost:8081/healthz
# → {"status":"healthy","service":"reservation"}

# 会员服务健康检查
curl http://localhost:8082/healthz
# → {"status":"healthy","service":"member"}

# 查询可预约时段
curl "http://localhost:8081/v1/stores/store-001/slots?date=2026-06-20"
# → [{"slot_id":"slot-store-001-0","table_type":"CAT_ZONE",...}]

# 创建预约
curl -X POST http://localhost:8081/v1/reservations \
  -H "Content-Type: application/json" \
  -d '{"customer_id":"cust-001","store_id":"store-001","slot_id":"slot-store-001-0","party_size":4}'
# → {"reservation_id":"resv-...","status":"PENDING_DEPOSIT"}

# 查询会员（自动创建）
curl "http://localhost:8082/v1/members?customerId=cust-001"
# → {"member_id":"mem-...","level":"GOLD","points_balance":15800}
```

## API 文档

启动后访问 Swagger UI：
- 预约服务: http://localhost:8081/docs
- 会员服务: 查看 D2-5 OpenAPI 契约文档

## 停服与清理

```bash
make down     # 停止并删除容器、网络、数据卷
docker compose down -v  # 同上
```

## 目录结构

```
.
├── .github/workflows/
│   ├── ci.yml              # CI 流水线 (Lint→Test→SAST→Build→Scan→Push)
│   └── cd.yml              # CD 流水线 (Deploy→Canary→Promote→Rollback)
├── services/
│   ├── reservation/
│   │   ├── Dockerfile       # 多阶段构建 (python:3.12-slim)
│   │   ├── requirements.txt
│   │   ├── src/main.py      # FastAPI 应用 (11个端点)
│   │   └── tests/test_smoke.py
│   └── member/
│       ├── Dockerfile       # 单阶段构建 (node:20-alpine)
│       ├── package.json
│       ├── src/index.js     # Express 应用 (9个端点)
│       └── tests/test_smoke.js
├── docker-compose.yml       # 本地4服务编排
├── Makefile                 # 快捷命令
├── .editorconfig
├── .pre-commit-config.yaml
└── docs/
    ├── rollback.md
    └── runbook.md
```

## 运行测试

```bash
# 预约服务测试
docker compose exec reservation pytest

# 会员服务测试
docker compose exec member npm test

# 全部测试
make test
```

## K8s 部署

```bash
# 部署到开发环境
helm upgrade --install nekocafe ./helm -f helm/values-dev.yaml -n dev --create-namespace

# 部署到生产环境（金丝雀 5%）
helm upgrade --install nekocafe ./helm -f helm/values-prod.yaml -n prod --create-namespace

# 回滚
helm rollback nekocafe -n prod
```
