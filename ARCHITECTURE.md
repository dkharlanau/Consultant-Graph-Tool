# Architecture decision: evidence-led execution graphs

Status: Accepted for educational prototype (2026-11-03)
Scope: Consultant Graph Tool v0.1, synthetic SAP Shipping pack.

## Decision and context

Consultants often investigate data or integration problems through repeated manual steps. The risky shortcut is to give an unconstrained LLM access to business systems and ask it to determine the cause and fix it.

We instead use a reusable LangGraph execution engine and domain-specific rule packs. The execution graph controls sequence, retries, evidence validation and explicit human review; each pack supplies pure validation and analysis. A future LLM adapter may explain existing evidence but must not independently assert missing configuration or perform writes.

## Responsibility boundary

| Responsibility | Owner | Example |
| --- | --- | --- |
| Input facts | Scenario pack / future read-only adapter | Sales order, material and ship-to records |
| Domain rules | Deterministic Python functions | Shipping point and route lookup in fixture |
| Orchestration | LangGraph StateGraph | Branching, retries, interruption and resume |
| Evidence gate | Engine | Checks that every finding cites existing fixture evidence IDs; not external fact verification |
| Proposed action | Pack, reported by engine | Check route determination customizing |
| Approval | Human interrupt | Approve or reject the *proposal* |
| Execution | Not implemented | **No SAP write capability** |
| Browser demo | Static JavaScript | Replays recorded Python execution traces |

## Node graph

```text
START -> load --(transient error; max 3 attempts)--> load
              |--(permanent source error)--> report --> END
              v
           validate --(invalid shape)--> diagnose
              |                            ^
              v                            |
           analyze ------------------------+
                                           |
                                        verify --(review requested)--> review --+
                                           |                                   |
                                           +-----------------------------------+--> report --> END
```

The actual graph is defined in `engine.py`; `cgt graph` exposes the simplified logical edge inventory. The UI is a *trace replay*, not a second implementation of the graph.

## SAP shipping pack boundary

For the **deliberately simplified classic determination case**:

- Shipping point proposal: delivering plant, effective shipping condition and material loading group.
- Route proposal: shipping point country/departure zone, ship-to country/transportation zone, effective shipping condition and material transportation group.
- The shipping point supplies the departure fields; they are not inferred from the material or sold-to record.
- A manually assigned alternative shipping point may be valid. Treat a difference as a review condition, not an automatic correction.
- Order document category, shipping point determination variants, extensions, SAP TM, STO and EWM are out of scope.
- Assignment of loading group and transportation group to the same value in the fixtures is a coincidence; they are different SAP master data attributes.
- A missing fixture rule means absent in **our synthetic data**, not proof that a specific customer's SAP system has no configuration.

Official references:
- [SAP Shipping Points](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/c7894a248ca14f74aca67f97528e5ad7/5d1bbf53d25ab64ce10000000a174cb4.html)
- [SAP Route Determination](https://help.sap.com/docs/SAP_S4HANA_ON-PREMISE/c7894a248ca14f74aca67f97528e5ad7/601bbf53d25ab64ce10000000a174cb4.html)
- [LangGraph Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)

## Human review

`--review` triggers a real LangGraph interrupt before approving any recommendation. The CLI can resume in the **same process** with `--decision approve` or `--decision reject`. The in-memory checkpointer does not provide durable cross-process approval. Production would require a durable store, identity-aware authorization, expiry, idempotency and execution preconditions.

There is no SAP write action, even after approval.

## Risks and controls

1. **False equivalence with SAP customizing.** Clearly label fixtures and link official documentation.
2. **Hallucinated recommendations.** No LLM required; all findings point to exact fixture evidence IDs.
3. **Unbounded investigations.** Fixed three-attempt retry budget, explicit stop outcomes.
4. **Unsupported recommendations.** If evidence IDs are missing, the report returns `insufficient_evidence`, withholds finding details and actions, and retains only finding codes for debugging. Passing the gate means reference coverage, not SAP truth.
5. **Unsafe write access.** No network connector or write executor in the MVP.
6. **Stale demo.** Regenerate browser traces with `cgt export-traces` from the same installed package being tested.
7. **Hidden data leakage.** Commit synthetic data only and never introduce credentials.

## Extension contract

A new pack implements `ScenarioPack`: `name`, `list_cases`, `get_case`, `validate`, `analyze`. Register it in the CLI and provide its own dataset/tests. The engine is domain-neutral, and the website reads a generic trace schema.

Future options (not implemented): JSON-only declarative scenario configuration, SAP read adapters, context-aware LLM explanation, persistent review, multi-tenant security and evaluation dashboards. Each should have separate tests and an explicit threat model.
