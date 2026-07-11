# Portfolio-Risk-Engine

Portfolio-Risk-Engine is a backend system for portfolio risk analytics. It ingests trade data, maintains portfolio positions, computes historical risk metrics, detects anomalous portfolio behavior, and classifies portfolio risk through a REST API.

![Architecture](docs/architecture.png)

A trade is sent to the API, validated, and saved to the database. The system then generates updated statistical features, computes historical risk metrics, detects anomalies, classifies the risk regime, and logs alerts if thresholds are breached.

## Why this project?

Portfolio-Risk-Engine was built to explore how backend systems and machine learning can work together in a production-style risk analytics pipeline. Instead of focusing on predicting market prices, the system focuses on processing streaming trades, generating portfolio analytics, detecting abnormal portfolio behavior, and serving low-latency risk assessments through a REST API.

During development, the project was profiled and optimized by batching database queries, moving feature computation into memory, and caching machine learning models, reducing end-to-end pipeline latency by nearly 4×.

## Core Features

* **Trade Ingestion**: Ingests individual trades, validates parameters, and logs executions in PostgreSQL.
* **Portfolio Position Tracking**: Compounds trade executions to maintain active portfolio holdings.
* **Historical Simulation VaR**: Calculates Value at Risk over rolling windows using historical portfolio price variations.
* **Feature Generation**: Computes rolling volatility, net and gross exposure, HHI concentration, and momentum indicators.
* **Anomaly Detection**: Trains on feature snapshots to detect outlier portfolios and anomalous exposure shocks.
* **Risk Classification**: Classifies the current portfolio risk into Low, Moderate, High, or Critical regimes using trained classifiers.
* **Redis Caching**: Caches active positions to minimize latency on frequent read requests.
* **Alert Generation**: Logs alerts for rule breaches, anomaly detections, and risk regime transitions.

## Technology Stack

| Component | Technology |
| :--- | :--- |
| API | FastAPI |
| Database | PostgreSQL |
| Cache | Redis |
| ML | Scikit-learn |
| ORM | SQLAlchemy |
| Testing | Pytest |

## Project Structure

```
Portfolio-Risk-Engine/
├── app/
│   ├── core/          # Redis and environment configuration
│   ├── models/        # SQLAlchemy database models
│   ├── routers/       # API route handlers
│   ├── schemas/       # Pydantic schema definitions
│   └── services/      # Business logic and risk analytics calculations
├── tests/             # Unit and integration tests
├── benchmark.py       # Pipeline latency benchmarking tool
├── docker-compose.yml # Docker multi-container settings
├── Dockerfile         # Container build instructions
└── requirements.txt   # Application dependencies
```

## API Endpoints

| Method | Route | Purpose |
| :--- | :--- | :--- |
| `POST` | `/portfolios` | Create a new portfolio |
| `GET` | `/portfolios/{id}/positions` | Fetch active holdings |
| `GET` | `/portfolios/{id}/var` | Calculate historical Value at Risk |
| `POST` | `/trades` | Ingest trade and run risk pipeline |
| `GET` | `/portfolios/{id}/trades` | Fetch transaction logs |
| `POST` | `/anomaly/train` | Train Isolation Forest model |
| `GET` | `/portfolios/{id}/anomaly/score` | Get latest anomaly score |
| `POST` | `/risk-classifier/train` | Train risk classifier models |
| `GET` | `/portfolios/{id}/risk-regime` | Get current risk regime classification |
| `POST` | `/portfolios/{id}/simulate` | Run mock trade sequence |
| `GET` | `/health` | Service health check |

## Design Decisions

* **PostgreSQL**: PostgreSQL is used to store trades, positions, features, and alerts. It ensures ACID compliance, transactional safety, and supports relational queries for portfolio auditing.
* **Redis**: Redis acts as an in-memory cache for active portfolio positions. It reduces database load and guarantees sub-millisecond read times for frequent queries.
* **Historical Simulation VaR**: This method computes risk directly from actual historical return distributions. It does not assume normal distributions, making it more accurate for portfolios with non-linear return profiles.
* **Isolation Forest**: This algorithm detects anomalies in portfolio features without requiring pre-labeled training data. It is well-suited for identifying multi-dimensional exposure outliers and sudden shocks.
* **Model Chaining**: The risk classifier combines engineered portfolio features with Historical VaR and Isolation Forest outputs. Separating statistical risk estimation, anomaly detection, and final classification makes the pipeline easier to interpret, debug, and extend.
* **Batched Feature Generation**: Recent trades and portfolio snapshots are fetched using batched database queries and processed in memory using NumPy, reducing database round trips and significantly lowering feature generation latency.
* **Cache Invalidation**: The system deletes the cache key when a new trade is committed to the database. This write-through invalidation avoids serving stale holdings while maintaining low-latency reads.

## Performance

The system includes a benchmark suite that simulates large-scale trade streams and measures latency across the complete analytics pipeline.

Benchmark configuration:
- 10,000 simulated trades
- 200 latency measurement runs

Results:

| Stage | Median Latency |
| :--- | ---: |
| Feature Generation | 15.9 ms |
| Anomaly Detection | 8.3 ms |
| Risk Classification | 19.8 ms |
| End-to-End Pipeline | **44.5 ms** |

Additional benchmark results:
- End-to-end P99 latency: **78.1 ms**
- 10,000 trade simulation completed in **267.8 seconds**

## Running Locally

The easiest way to run the application and its database/cache dependencies is via Docker Compose:

1. Clone the repository:
```bash
git clone https://github.com/anushaanand60/Portfolio-Risk-Engine.git
cd Portfolio-Risk-Engine
```

2. Start the services:
```bash
docker compose up --build
```

## Running Tests

Run the test suite using pytest. Ensure your PYTHONPATH is set:
```bash
$env:PYTHONPATH="."
pytest
```

## Benchmark

The `benchmark.py` script generates mock trades, trains the anomaly detector and risk classifier, and measures step latencies for trade ingestion, feature generation, anomaly scoring, and risk classification.

To run the benchmark:
```bash
python benchmark.py --trades 10000 --latency-runs 200
```

## Future Improvements

- Streaming ingestion using Kafka
- Incremental online feature computation
- Online anomaly model updates
- Distributed benchmark execution