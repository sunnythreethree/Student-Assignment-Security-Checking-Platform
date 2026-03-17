# CS6620 Group 9 — Project Overview
## Student Assignment Security Checking Platform

---

## 三人完整分工

### Jingsi — API 层 + 基础设施核心
> 负责所有请求进出口和基础设施

| 任务 | 具体内容 |
|------|---------|
| Lambda A | `POST /scan` 输入验证、生成 `scan_id`、写 SQS + DynamoDB |
| Lambda A | `GET /status` 查询 DynamoDB 返回扫描状态 |
| Lambda Function URL | 配置 TLS 入口，替代 API Gateway |
| SQS Queue | Standard Queue + DLQ（死信队列，失败重试） |
| IAM Role | Lambda 执行角色，权限：SQS / DynamoDB / S3 |
| CloudWatch | Alarm（SQS queue depth）+ Dashboard |
| Deploy 脚本 | 打包 Lambda A + 部署 |

---

### Jiahua — 扫描引擎 + 容器
> 负责实际跑扫描的部分

| 任务 | 具体内容 |
|------|---------|
| Lambda B | 消费 SQS 消息，提取代码 |
| Bandit 集成 | 在 Lambda 里跑 Bandit 扫描 Python 代码 |
| Semgrep 集成 | 扫描 Java / JS 代码 |
| Dockerfile | 打包 Bandit + Semgrep 镜像（ECS Fargate 备选） |
| S3 写入 | 把扫描结果 JSON 写到 S3 |
| ECS Fargate | 配置 Task Definition（大文件 >3GB RAM 时使用） |

---

### Mengshan — 数据层
> 负责数据的结构化和存储

| 任务 | 具体内容 |
|------|---------|
| DynamoDB Schema | 设计表结构，PK / SK 选择 |
| `result_parser.py` | 把 Bandit 输出和 Semgrep 输出标准化成同一格式 |
| S3 Bucket 策略 | 报告存储结构、presigned URL 生成 |
| 历史查询 | 按 `student_id` 查所有历史扫描 |
| Frontend 数据渲染 | 结果展示的数据结构定义 |

---

## 完整文件结构

```
sast-platform/
│
├── README.md
├── .gitignore
│
├── frontend/                          ← S3 静态托管
│   ├── index.html                     # 主页面：代码输入框 + 结果展示
│   ├── css/
│   │   └── style.css
│   └── js/
│       ├── app.js                     # 提交逻辑 + 轮询 /status
│       └── results.js                 # 渲染漏洞报告（severity badges）
│
├── lambda_a/                          ← [Jingsi]
│   ├── handler.py                     # 入口：路由 POST / GET 请求
│   ├── validator.py                   # 验证：code / language / size / student_id
│   ├── dispatcher.py                  # 写 SQS + DynamoDB (status: PENDING)
│   ├── status.py                      # 查 DynamoDB，返回状态 + presigned URL
│   └── requirements.txt              # boto3（Lambda runtime 自带，供本地测试用）
│
├── lambda_b/                          ← [Jiahua]
│   ├── handler.py                     # 入口：从 SQS event 取消息
│   ├── scanner.py                     # 调用 Bandit / Semgrep，返回原始输出
│   ├── result_parser.py               # [Mengshan] 标准化输出格式
│   ├── s3_writer.py                   # 写报告到 S3
│   ├── Dockerfile                     # ECS Fargate 镜像
│   └── requirements.txt              # bandit, semgrep, boto3
│
├── tests/
│   ├── unit/
│   │   ├── test_validator.py          # [Jingsi] 验证逻辑单测
│   │   ├── test_dispatcher.py         # [Jingsi] SQS / DynamoDB 写入单测
│   │   ├── test_scanner.py            # [Jiahua] 扫描逻辑单测
│   │   └── test_result_parser.py      # [Mengshan] 解析逻辑单测
│   ├── integration/
│   │   └── test_sqs_pipeline.py       # Lambda A → SQS → DynamoDB 联通测试
│   ├── e2e/
│   │   └── test_full_scan_flow.py     # 完整流程：提交 → 扫描 → 查询结果
│   └── load/
│       └── locustfile.py              # 并发测试，验证 SQS 缓冲效果
│
├── infrastructure/
│   ├── sqs.yaml                       # [Jingsi] SQS Queue + DLQ
│   ├── dynamodb.yaml                  # [Mengshan] DynamoDB table
│   ├── s3.yaml                        # [Mengshan] S3 bucket（报告 + 前端）
│   ├── lambda_a.yaml                  # [Jingsi] Lambda A + Function URL
│   ├── lambda_b.yaml                  # [Jiahua] Lambda B + SQS trigger
│   ├── ecs.yaml                       # [Jiahua] ECS Fargate task definition
│   ├── cloudwatch.yaml                # [Jingsi] Alarms + Dashboard
│   └── iam_roles.yaml                 # [Jingsi] Lambda 执行角色
│
└── scripts/
    ├── 01_setup_infra.sh              # 按顺序部署所有 CloudFormation stacks
    ├── 02_deploy_lambda_a.sh          # zip lambda_a/ → aws lambda update
    ├── 03_deploy_lambda_b.sh          # zip lambda_b/ → aws lambda update
    ├── 04_upload_frontend.sh          # aws s3 sync frontend/ → S3 bucket
    └── 05_test_api.sh                 # curl 冒烟测试
```

---

## 关键接口约定

> 这是三人之间的"合同"，各自独立开发时必须遵守。

### POST /scan — 请求体

```json
{
  "code": "import os\nos.system(input())",
  "language": "python",
  "student_id": "neu123"
}
```

### HTTP 202 响应（Lambda A 返回）

```json
{
  "scan_id": "scan-abc123",
  "status": "PENDING"
}
```

### GET /status — 响应体

```json
{
  "scan_id": "scan-abc123",
  "status": "DONE",
  "vuln_count": 3,
  "report_url": "https://s3.presigned.url/..."
}
```

### DynamoDB 记录结构
> Jingsi 写入初始记录，Mengshan 设计 schema，Jiahua 更新 DONE 状态

```json
{
  "student_id": "neu123",
  "scan_id":    "scan-abc123",
  "status":     "PENDING | DONE | FAILED",
  "language":   "python",
  "created_at":   "2025-03-17T10:00:00Z",
  "completed_at": "2025-03-17T10:00:05Z",
  "vuln_count":   3,
  "s3_report_key": "reports/scan-abc123.json"
}
```

### S3 报告 JSON 格式
> Jiahua 生成，Mengshan 定义结构

```json
{
  "scan_id":  "scan-abc123",
  "language": "python",
  "tool":     "bandit",
  "findings": [
    {
      "line":         5,
      "severity":     "HIGH",
      "confidence":   "MEDIUM",
      "issue":        "Use of exec detected",
      "code_snippet": "exec(user_input)"
    }
  ],
  "summary": {
    "HIGH":   1,
    "MEDIUM": 2,
    "LOW":    0
  }
}
```

---

## Git 协作规范

```
main
├── feature/lambda-a        ← Jingsi
├── feature/lambda-b        ← Jiahua
└── feature/data-layer      ← Mengshan
```

- 每人只动自己负责的目录，`infrastructure/` 里各自只改自己的 yaml
- PR → main 需要至少一人 review
- 接口约定（见上）变更必须三人确认后才能合并
