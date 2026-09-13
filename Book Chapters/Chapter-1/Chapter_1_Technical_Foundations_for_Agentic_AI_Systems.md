# Chapter 1: Technical Foundations for Agentic AI Systems

This book is about end-to-end agentic AI systems for banking, not standalone models. It does not attempt to teach the mathematics behind AI, how a large language model is trained, or every detail of prompt engineering.

Instead, this chapter gives readers the technical foundation needed to follow the business discussions, architecture diagrams, API contracts, data models, and implementation conversations in the chapters ahead. It is written for engineers, data scientists, architects, consultants, business stakeholders, and interview candidates who need a shared, practical understanding of how these systems work.

For a broader, hands-on introduction to large language models, retrieval-augmented generation, prompts, and implementation techniques, readers may refer to *Complete Hands-On Large Language Models Playbook*.

---

## 1.1 The Technical System Behind an AI Agent

An AI agent is not just a chat window or a model response. It is one component in a larger software system.

A typical banking request might sound simple: “Read this annual report, extract the important financial values, calculate the ratios, identify risks, and prepare a draft note.” In practice, completing that request safely requires much more than sending a PDF to an LLM.

The system must know who submitted the document, whether the user is allowed to access it, whether the document is readable, where each extracted value came from, whether the value is valid, how calculations were performed, whether a person needs to review the result, and how to reproduce the result later.

The case studies in this book therefore focus on **end-to-end systems**. Each system combines AI capabilities with regular software, data storage, validation, human review, and audit records.

The major components readers will see in every case study are:

- **User interface**: The screen where an analyst, relationship manager, compliance officer, credit analyst, or portfolio manager submits information, reviews results, and takes action.
- **API layer**: The formal interface through which the user interface and other systems send requests and receive responses.
- **Agent orchestrator**: The component that coordinates the overall process and decides which approved task should run next.
- **LLM gateway**: The controlled entry point for calls to language models. It manages model selection, prompts, limits, retries, and logging.
- **Retrieval service**: The component that finds relevant pages, passages, records, or documents before an LLM is asked to read or summarize them.
- **Vector database or search index**: A search system that helps find information by words, phrases, or similarity of meaning.
- **Deterministic calculation engine**: Regular software that performs repeatable calculations, such as financial ratios, credit metrics, rankings, and portfolio measures.
- **Rule engine**: A service that applies explicit business rules, thresholds, and policies consistently.
- **Database**: The main system of record for users, cases, facts, scores, decisions, approvals, and history.
- **Object storage**: Storage for large files such as PDFs, images, scanned documents, and generated reports.
- **Background worker and queue**: Components that process long-running tasks, such as OCR, document extraction, external screening, and report generation, without making the user wait on one web request.
- **Validation service**: A service that checks inputs, model outputs, citations, calculations, and business rules before results can move forward.
- **Human-review workflow**: The controlled process through which authorized people review, approve, reject, or override an output.
- **Audit-event store**: A history of important actions, including document uploads, agent runs, tool calls, validation results, calculations, reviewer actions, and overrides.
- **External data integrations**: Approved links to outside or internal data sources, such as market data, sanctions lists, PEP sources, core banking systems, CRM systems, and policy repositories.

A simplified view of the architecture looks like this:

```text
User or analyst
      |
      v
User interface and API layer
      |
      v
Authentication, authorization, and input validation
      |
      v
Workflow and agent orchestrator
      |
      +------------------------------+
      |                              |
      v                              v
LLM and agent services         Deterministic services
      |                              |
      v                              v
Retrieval, tools, and data     Rules, scores, and checks
      |                              |
      +--------------+---------------+
                     |
                     v
      Validation, human review, and approval
                     |
                     v
Database, storage, logs, and audit records
```

The rest of this chapter explains these components and why they are needed.

---

## 1.2 LLMs, Workflows, and Agents

The terms LLM, workflow, RAG, agent, and multi-agent system are often used as if they mean the same thing. They do not. This book uses them in the following practical way.

| Term | Meaning in This Book | Example |
|---|---|---|
| LLM call | One prompt produces one response | Summarize a document |
| Workflow | A predefined sequence of software steps | Upload, OCR, extract, validate, calculate |
| RAG system | Retrieves evidence before the LLM responds | Find relevant filing pages before extraction |
| AI agent | Uses approved tools and adapts its next step based on what it finds | An extraction agent retrieves, checks, and retries on relevant pages |
| Multi-agent system | Several specialized agents work under an orchestrator | One agent per statement type or risk task |
| Deterministic service | Regular software that gives the same output for the same input | Calculate a leverage ratio or apply a risk rule |
| Human-review workflow | A controlled stage where an authorized person reviews, approves, rejects, or overrides a result | Compliance officer reviews a high-risk KYC case |

### LLM call

An LLM call is the simplest use of a language model. The application sends an instruction and receives one response.

For example:

> Summarize the key financial risks described in this annual report.

This can be helpful, but it is not enough for most banking workflows. The response may be useful as a draft, but the system still needs to validate important claims, preserve source evidence, and route the result to the right reviewer.

### Workflow

A workflow is a known series of steps completed in a defined order.

For example:

```text
Upload document
  -> identify document type
  -> extract native text
  -> apply OCR when needed
  -> find relevant pages
  -> extract values
  -> validate evidence
  -> calculate ratios
  -> create draft narrative
  -> send for review
```

A workflow can include AI. It is still a workflow when the sequence is mostly fixed and decided in advance.

### RAG system

RAG stands for retrieval-augmented generation. In simple terms, the system looks for relevant information before asking the LLM to answer.

For example, instead of giving an LLM a full 200-page annual report and asking it to find total debt, the system first searches for pages related to debt, borrowings, liabilities, or the balance sheet. It then gives the most relevant pages to the model.

This helps the model focus on evidence. It can reduce cost, improve speed, and lower the chance that the model ignores an important passage. It does not remove the need for validation.

### AI agent

An AI agent uses an LLM to work toward a goal through multiple controlled steps.

For example, a document-extraction agent may receive the task: “Find total debt for the latest reporting period.” It may search the document, inspect the returned pages, decide that the evidence is incomplete, run a second search using different terms, and then return an extracted value with the source page.

The agent is not free to do anything it wants. A banking agent should only use approved tools, operate within a defined scope, stop after a limited number of attempts, and route uncertain cases to review.

### Multi-agent system

A multi-agent system uses separate agents for separate tasks.

For example, the Financial Document Extraction Agent may use one agent to identify company and reporting-period information, another to extract the balance sheet, another to extract the income statement, another to identify qualitative risks, and another to produce a draft narrative using validated facts.

This is useful when tasks require different instructions, evidence, or checks. It is not always necessary. More agents can increase cost, processing time, and design complexity. The right design is the smallest number of components that can complete the task reliably.

### Deterministic service

A deterministic service is ordinary software that produces the same output whenever it receives the same input.

For example, if total debt is 200 and shareholders’ equity is 100, a deterministic calculation engine will always calculate a debt-to-equity ratio of 2.0.

Deterministic services are used for calculations, scores, rules, rankings, thresholds, constraints, and state changes because these results must be reproducible and explainable.

### Human-review workflow

A human-review workflow is the part of the system where an authorized person examines the evidence and takes responsibility for the next action.

For example, a compliance officer may review a high-risk KYC case. The system can collect documents, identify related entities, run approved screening checks, calculate risk factors, and write a summary. The compliance officer decides whether to approve the onboarding, request more information, reject the case, or escalate it to an MLRO.

The practical distinction is simple:

- An LLM reads, extracts, summarizes, and explains language.
- A workflow coordinates known steps.
- RAG retrieves evidence before the model responds.
- An agent can choose from approved next steps after inspecting evidence.
- A multi-agent system divides complex work across specialized agents.
- Deterministic software performs calculations, applies rules, and enforces constraints.
- People remain accountable for consequential decisions, exceptions, and overrides.

---

## 1.3 The Core Components Used in This Book

### LLM Gateway

An LLM gateway is the controlled layer through which the application calls one or more language models. Rather than letting every part of the application call a model directly, the gateway provides a single, managed path.

This matters because a banking system may need to control which model is used, what information is sent to it, how much the call costs, how long the system waits, and what happens when the model provider is unavailable.

The LLM gateway covers:

- **Model selection**: Choosing an appropriate model for a task, such as document extraction, classification, or narrative generation.
- **Prompt templates**: Reusable instructions that ensure the same task is described consistently each time.
- **Structured output**: Asking the model to return information in a defined shape, such as fields for value, currency, period, source page, and confidence.
- **Token and cost controls**: Limiting how much text is sent to and received from the model so that processing cost remains predictable.
- **Timeouts and retries**: Stopping a call that takes too long and retrying controlled failures where appropriate.
- **Provider fallback**: Using an approved alternative model or provider if the preferred service is unavailable.
- **Model-version capture**: Recording which model version was used for an output.
- **Request and response logging**: Preserving enough information to investigate model behavior while protecting sensitive content appropriately.
- **Sensitive-data masking**: Removing or masking unnecessary personal or confidential information before a request is sent to a model, where required.

### Agent Orchestration

Agent orchestration is the coordination layer for a multi-step process. It decides what should happen next, which tool or service should perform the task, and when the process should stop or wait for human review.

For example, a KYC assessment may move through document processing, entity extraction, ownership-graph construction, screening, risk scoring, and compliance review. The orchestrator coordinates these steps and records the current state of the case.

The orchestration layer covers:

- **State machines**: A clear list of allowed stages, such as submitted, processing, validating, awaiting review, approved, or failed.
- **Tool routing**: Choosing which approved tool should be used for a task.
- **Task sequencing**: Running steps in the correct order.
- **Agent handoffs**: Passing a validated result from one specialized agent to another.
- **Guardrail checkpoints**: Pausing progress until important validation or approval checks pass.
- **Stop conditions**: Ending a task after success, a defined number of attempts, or a known failure condition.
- **Failure and retry paths**: Defining what happens when a step fails temporarily or cannot be completed.
- **Human-review routing**: Sending uncertain, high-risk, or policy-sensitive cases to the correct reviewer.
- **Workflow replay and rerun handling**: Allowing authorized users to rerun a case while preserving the history of earlier runs.

### Retrieval and RAG

Retrieval is how the system finds useful evidence before it asks an LLM to reason over or summarize information.

A long document may contain hundreds of pages. A customer case may include dozens of files. A research platform may contain years of filings, notes, and market data. Retrieval narrows this material to the content relevant to the task.

The retrieval and RAG layer covers:

- **Document chunking**: Breaking a long document into manageable pieces, usually by page, section, or paragraph.
- **Metadata extraction**: Recording useful labels such as company, client, document type, fiscal year, date, country, or business unit.
- **Embeddings**: Numerical representations that help a search system find text with similar meaning even when exact words differ.
- **Vector search**: Searching by similarity of meaning.
- **Keyword search**: Searching for exact words and phrases.
- **Hybrid retrieval**: Combining semantic and keyword search for more reliable results.
- **Citation capture**: Keeping the source document, page, section, or record that supports a returned fact.
- **Re-ranking**: Reordering initial search results so the most useful evidence is presented first.
- **Source filtering**: Restricting results to approved, authorized, relevant, or recent sources.
- **Retrieval-quality checks**: Detecting when the system did not find enough relevant evidence and should retry or route to review.

### Document Processing and OCR

Documents that people can read are not always directly usable by software.

Some PDFs contain native text. The words can be highlighted, copied, and searched. Other PDFs are scanned images, which means a computer sees a picture of a page rather than readable text. Some documents contain tables, charts, signatures, stamps, or ownership diagrams that need special handling.

Document processing and OCR cover:

- **Native-text extraction**: Reading text directly from digital documents.
- **OCR for scans and image-based documents**: Converting an image of text into text that software can use. OCR stands for optical character recognition.
- **Page classification**: Identifying whether a page contains usable text, an image, a table, a chart, or a document type that needs a different processing path.
- **Table extraction**: Connecting values to the correct rows, columns, headings, periods, units, and currencies.
- **Layout-aware parsing**: Using the position of text and visual layout to understand documents that do not follow a simple paragraph structure.
- **Confidence scoring**: Estimating whether extracted content is likely to be reliable enough for the next step.
- **Source-page attribution**: Preserving the page from which an extracted fact was obtained.
- **Image and document quality checks**: Detecting blurred, rotated, incomplete, password-protected, or otherwise difficult files.
- **Document-type classification**: Identifying whether a file is an annual report, bank statement, trust deed, passport, ownership chart, research report, or another relevant document type.

### Deterministic Services

Deterministic services are regular software components used for work that must be consistent, testable, and explainable.

They should own tasks where there is a formula, a rule, a threshold, or a clear expected result. In the systems described in this book, the LLM may help find or interpret source information, but deterministic services own the decision-bearing processing.

Deterministic services cover:

- **Financial calculations**: Revenue growth, margins, leverage, coverage ratios, free cash flow, and related metrics.
- **Rule execution**: Applying defined policy rules and decision logic.
- **Risk scoring**: Combining approved risk factors and weights into an explainable score.
- **Ranking**: Ordering companies, securities, or cases based on defined criteria.
- **Portfolio constraints**: Checking concentration, exposure, liquidity, sector, mandate, or other limits.
- **Graph traversal**: Following ownership links across people, companies, trusts, and foundations to identify beneficial ownership.
- **Threshold checks**: Flagging values that exceed, fall below, or otherwise violate an approved boundary.
- **Schema validation**: Checking whether data follows the required structure and contains valid types and values.
- **Duplicate detection**: Identifying repeated documents, repeated requests, or possible duplicate records.
- **State-transition validation**: Ensuring a case cannot move to an invalid stage, such as published before required approval.

### Databases and Storage

A banking AI system needs several forms of memory. It must store original files, structured facts, calculations, workflow states, review actions, and audit records.

Databases and storage cover:

- **Relational databases**: Structured tables that represent relationships, such as a client with many assessments or a company with many documents.
- **Document stores**: Flexible storage for records that do not fit naturally into fixed tables.
- **Object storage**: Durable storage for large files such as PDFs, scans, images, and generated reports.
- **Vector databases**: Search storage that supports finding content by semantic similarity.
- **Caching**: Temporary storage used to make repeated reads faster, without replacing the permanent source of truth.
- **Event logs**: A chronological record of important system events.
- **Immutable audit trails**: Records designed so that earlier events cannot be silently changed or removed.
- **Reference-data storage**: Storage for approved lists, thresholds, scoring weights, country classifications, policy values, and other controlled data.
- **Versioned configuration storage**: Storage for versions of rules, prompts, model settings, and workflow configuration.

### External Data Integrations

Many banking workflows depend on information outside the immediate application. These sources must be integrated carefully, because the system needs to know what source was used, when it was accessed, and whether the user or agent was allowed to use it.

External data integrations may include:

- **Sanctions and watchlists**: Lists used to identify sanctioned persons, entities, or countries.
- **PEP and adverse-media sources**: Sources used to identify political exposure and potentially relevant negative information.
- **Company and financial data**: Corporate filings, financial statements, reference data, and business information.
- **Market data**: Prices, volumes, benchmarks, interest rates, and other market information.
- **Internal customer systems**: Customer records, account information, and relationship history.
- **Core banking or lending systems**: Loan, facility, repayment, collateral, and transaction data.
- **CRM and case-management systems**: Relationship-manager information, tasks, approvals, and case notes.
- **Regulatory and policy repositories**: Internal policies, procedures, regulatory guidance, and approved controls.

---

## 1.4 APIs, Asynchronous Processing, and Event-Driven Workflows

Not all system tasks finish while a user waits. Uploading a document might take a few seconds. Processing a large, scanned annual report may take several minutes because the system needs to extract text, run OCR, search for evidence, call specialized agents, validate outputs, calculate metrics, and create a review package.

This section explains how systems handle both fast requests and long-running work.

### APIs and Service Contracts

An API, or application programming interface, is a defined way for one piece of software to ask another piece of software to perform a task.

For example, a user interface may send a request to create a document-processing job. The API states what information the request must contain and what the response will look like.

API and service contracts cover:

- **Request and response formats**: The fields the caller sends and the fields the service returns.
- **Input validation**: Checking that requests include required values and use acceptable formats.
- **Error responses**: Returning clear information when the request cannot be processed.
- **Authentication and authorization**: Confirming who made the request and whether they are allowed to perform it.
- **API versioning**: Managing changes to an API without breaking systems that depend on it.
- **Idempotency keys**: A unique request identifier that prevents one repeated request from creating repeated work or duplicate records.
- **Rate limits**: Controlling how many requests a user or service can make in a period of time.
- **Correlation IDs**: A shared identifier used to follow one request across the user interface, API, workers, agents, databases, and audit logs.

### Synchronous Requests

A synchronous request is a request where the user waits for an immediate response.

Synchronous requests should be short and predictable. Examples include:

- User submits a request
- API validates the request
- API creates or updates a record
- API returns a quick response
- User receives a case ID, document ID, or job ID

For example, when an analyst uploads an annual report, the system may accept the file, save it, create a processing job, and return a job ID. The analyst does not need to wait for every extraction step to finish before receiving confirmation that the upload succeeded.

### Asynchronous Jobs

An asynchronous job continues in the background after the initial user request has returned.

This pattern is useful for work that may take longer or depend on several services.

Asynchronous jobs include:

- Long-running document processing
- OCR
- Multi-agent extraction
- External screening
- Batch calculations
- Research generation
- Portfolio analytics
- Stress testing
- Report generation

A common pattern is:

```text
User uploads a document
  -> API accepts the document
  -> API creates a processing job
  -> API returns a job ID immediately
  -> a background worker processes the job
  -> the user checks status or receives a notification
```

### Queues, Workers, and Job States

A queue is a controlled waiting line for background tasks. A worker is a process that takes work from the queue and performs it.

Queues prevent a sudden burst of work from overwhelming the main application. For example, during an earnings season, many analysts may upload reports at the same time. A queue allows the system to process them in an orderly way.

A job may move through the following states:

- **Job submission**: A request creates a new job.
- **Queued**: The job is waiting for a worker.
- **Processing**: The worker is carrying out the main task.
- **Validating**: The system is checking whether results are complete and supported by evidence.
- **Awaiting review**: A human must inspect the result before it can move forward.
- **Completed**: The task finished successfully.
- **Failed**: The task could not be completed.
- **Retried**: The system is attempting the task again after a temporary problem.
- **Cancelled**: An authorized user or system stopped the task.

Clear job states help users understand progress and help engineering teams identify where a failure occurred.

### Event-Driven Workflow Progression

An event-driven workflow moves forward when something meaningful happens. The completion of one step can safely trigger the next step.

For example:

```text
Document uploaded
  -> document processing starts
  -> text extraction completes
  -> retrieval index is built
  -> extraction agents run
  -> validation completes
  -> deterministic calculations run
  -> case is routed to review
```

This does not always require a complicated event-streaming platform. The important idea is that the system knows which event occurred, records it, and starts the next allowed step without relying on a user to manually trigger every stage.

### Why Idempotency Matters

Idempotency means that repeating the same request does not create an unintended duplicate result.

This matters because requests can be repeated for ordinary reasons:

- Duplicate uploads
- Browser retries
- Worker restarts
- Partially completed workflows
- Safe reprocessing
- Duplicate case creation
- Preventing repeated external tool calls

For example, if a user clicks the upload button twice because their connection is slow, the system should not process the same document twice and create two conflicting records. It should recognize the repeated request or file and handle it safely.

---

## 1.5 Data, Database Design, and Auditability

A trustworthy banking system must preserve more than the final output. It must preserve original evidence, extracted facts, calculations, rules, system versions, and human decisions.

### Structured and Unstructured Data

Structured data is already organized into known fields that software can use directly. Examples include a customer ID, a loan amount, a country code, a market price, or a table of approved risk weights.

Unstructured data does not arrive in a fixed table. It often needs document processing, search, or language understanding before it becomes useful.

The systems in this book work with both kinds of data:

- PDFs
- Images
- Tables
- Financial statements
- Customer documents
- Watchlists
- Market data
- User inputs
- Analyst notes
- Research reports
- Ownership diagrams
- Internal policy documents

### Source of Truth and Derived Data

A system should clearly separate original evidence from information produced later in the workflow.

- **Raw document**: The original file or source received by the system.
- **Extracted fact**: A value or statement taken from the source, such as revenue reported on page 47.
- **Validated fact**: An extracted fact that passed format, evidence, and business checks.
- **Derived metric**: A value calculated from validated facts, such as revenue growth or interest coverage.
- **Rule result**: The result of applying a defined policy or threshold.
- **Decision record**: A recommendation, approval, rejection, escalation, or other recorded outcome.
- **Narrative output**: A written explanation, summary, research note, credit memo, or review package.
- **Human override**: A documented change made by an authorized person to a system recommendation or outcome.

For example:

```text
Raw document:
Annual report, page 47

Extracted facts:
FY2025 revenue: USD 5.2 billion
FY2024 revenue: USD 4.64 billion

Derived metric:
Revenue growth = (5.2 - 4.64) / 4.64 = 12.1%

Narrative output:
Revenue increased by 12.1% year over year.
```

The raw document supports the extracted facts. The validated facts support the derived metric. The derived metric supports the narrative. Keeping these stages separate makes the result easier to verify.

### Data Lineage and Provenance

Data lineage and provenance describe where information came from and how it moved through the system.

A system should be able to answer questions such as:

- Which source produced a fact?
- Which page supports the fact?
- Which agent extracted it?
- Which model and prompt version were used?
- Which deterministic calculation consumed it?
- Which rule version applied?
- Which reviewer approved or overrode it?
- When did each action occur?

For example, if a credit reviewer asks why a rating changed, the system should be able to show the financial statements used, the extracted values, the calculations, the scorecard version, the stress-test assumptions, and the reviewer’s final decision.

### Versioning

Systems change. Documents are restated. Risk weights are updated. Prompts improve. Models change. A good system must preserve enough history to reproduce or explain an earlier result under the conditions that existed at the time.

Versioning includes:

- Document versions
- Model versions
- Prompt versions
- Rule versions
- Reference-data versions
- Schema migrations
- Rerun history
- Review and approval versions
- Configuration history

For example, a KYC assessment performed today may use a different country-risk table from the one used two years ago. Both assessments should preserve the version of the table that was active at the time.

---

## 1.6 Reliability, Validation, and Failure Handling

Banking systems must be prepared for incorrect inputs, missing documents, poor-quality scans, model errors, unavailable external services, duplicate requests, and unexpected edge cases.

The goal is not to claim that every output will always be correct. The goal is to design the system so that it detects uncertainty, handles failures safely, and makes problems visible to the right people.

### Input Validation

Input validation checks whether the system received something it can process safely.

It includes:

- File-type checks
- File-size checks
- Required-field checks
- User-permission checks
- Duplicate detection
- Malware and document safety checks

For example, a KYC workflow should not accept an unsupported file type and then silently ignore it. It should tell the user that the file cannot be processed and explain what action is needed.

### Structured Output Validation

An LLM response should be checked before it becomes trusted system data.

Structured output validation covers:

- Required fields
- Data types
- Number formats
- Currency and period formats
- Allowed values
- Schema conformance

For example, if the system expects a financial value, currency, reporting period, and source page, it should reject a response that only says, “Revenue seems to be around 5.2 billion.”

### Citation and Evidence Validation

An important claim should be supported by evidence.

Citation and evidence validation covers:

- Source-page requirement
- Evidence-to-claim matching
- Direct evidence versus inferred output
- Missing-evidence handling
- Unsupported-claim rejection

If an extraction agent says total debt is a particular amount and cites page 58, the system should check whether page 58 actually contains the relevant value and label. A confident citation is not enough by itself.

### Confidence Thresholds and Reasonableness Checks

Some results may pass a format check but still look suspicious. Confidence and reasonableness checks help identify these cases.

They cover:

- Low-confidence extraction handling
- Large changes between reporting periods
- Impossible or inconsistent values
- Missing values in required calculations
- Cross-field reconciliation

For example, if revenue jumps from 500 million to 50 billion, the system should not automatically assume the number is wrong. But it should flag the change for review because the result may indicate a unit mismatch, a misplaced decimal, or a wrong column selection.

### Retry and Backoff

Some failures are temporary. A model provider may be slow, an external screening source may be unavailable, or a network call may time out.

Retry and backoff include:

- Temporary network failures
- Rate-limited model providers
- Unavailable external data sources
- Controlled retry limits
- Exponential waiting time between retries

Exponential backoff means waiting longer between attempts, such as one second, then two seconds, then four seconds. This reduces pressure on an already struggling service.

### Fallback Paths

A fallback path defines what the system does when the preferred path cannot complete the task.

Fallback options include:

- Retry with an alternative model
- Retry using a different retrieval method
- Route document pages to OCR
- Use a simpler deterministic parser
- Route the case to manual review
- Return a clear incomplete-result status

A fallback should be chosen based on the risk of the use case. For a high-risk KYC result, routing to a human is often safer than trying increasingly aggressive automated alternatives.

### Manual Review Queues

Some cases should be reviewed by a person rather than automatically completed.

Manual review queues may contain:

- Low-confidence results
- High-risk cases
- Conflicting data
- Failed validation
- Unclear ownership structures
- Policy exceptions
- High-value or high-impact outcomes

The review queue should show the reviewer why the case was routed there and what evidence is available.

### Fail Loud, Never Fail Silent

This is one of the central principles used throughout the case studies:

> If the system cannot find, validate, calculate, or verify an important piece of information, it must clearly report the problem and route the case appropriately. It must not quietly guess, omit the issue, or produce a falsely complete output.

For example, if interest expense cannot be validated from a financial report, the system should state that interest coverage cannot be calculated and request review. It should not assume interest expense is zero and report an artificially strong ratio.

### Observability

Observability means giving the organization enough visibility to understand what the system did, how it is performing, and why it failed.

Observability includes:

- Application logs
- Agent and tool-call logs
- Metrics
- Processing-time measurement
- Error-rate monitoring
- Cost monitoring
- Request traces
- Alerts for failed or stuck jobs
- Dashboards for review queues and backlog

For example, an operations team should be able to see if OCR jobs are taking much longer than usual, if a model provider is failing, if document extraction quality has declined, or if the compliance review queue is growing faster than the team can process it.

---

## 1.7 Security and Access Control Basics

Banking systems handle sensitive customer, financial, research, and risk data. Security boundaries therefore shape the architecture from the beginning.

### Authentication and Authorization

Authentication confirms who a user or service is. Authorization checks what that user or service is allowed to do.

These controls include:

- Confirming user identity
- Checking user permissions
- Service-to-service authentication
- Session management
- API access tokens

For example, a relationship manager may be allowed to create a KYC case but not approve a high-risk case. A compliance officer may review the case but not change the organization’s risk-scoring policy.

### Role-Based Access Control

Role-based access control assigns permissions based on a person’s role.

Typical roles in the systems described in this book include:

- Analyst
- Reviewer
- Compliance officer
- MLRO
- Credit officer
- Portfolio manager
- Administrator
- Auditor

The purpose is not to make access complicated. It is to make responsibilities clear and prevent people from taking actions outside their authority.

### Least-Privilege Tool Access

Least privilege means that each user, service, and agent receives only the access needed to complete its job.

For example:

- An extraction agent may read an approved document and write extracted facts.
- An extraction agent should not approve a credit rating.
- A screening service may query an approved watchlist.
- A screening service should not change the watchlist.
- An AI agent may draft a research narrative.
- An AI agent should not execute a trade or change a portfolio mandate.

Least-privilege tool access includes:

- Read-only document access for extraction agents
- No approval rights for AI agents
- Restricted screening-service access
- Restricted write access to systems of record
- Permission checks before external tool calls
- Approval gates for consequential actions

### Secrets Management

A secret is sensitive information used by systems to authenticate or encrypt data. Examples include API keys, passwords, and encryption keys.

Secrets management includes:

- API keys
- Database passwords
- External vendor credentials
- Model-provider credentials
- Encryption keys
- Rotation and revocation

Secrets should not be stored in source code, prompts, or plain-text configuration files.

### Sensitive Data and PII Handling

PII means personally identifiable information, such as a person’s name, address, identity number, date of birth, or passport details.

Banking systems may also handle confidential business information, internal research, ownership structures, account information, and credit assessments.

Sensitive-data handling includes:

- Customer identity documents
- Account and transaction data
- Financial statements
- Ownership information
- Internal research
- Data masking
- Encryption
- Retention limits
- Approved data locations

The design should avoid sending unnecessary sensitive data to an LLM or an external service. If a model only needs a document section to extract one field, the system should not automatically send the full customer record.

### Tenant and Data Isolation

Tenant and data isolation ensures that one organization, business unit, team, or client cannot access another organization’s data without authorization.

It includes:

- Separating data by organization, business unit, or client
- Preventing cross-client document access
- Restricting search results to authorized data
- Avoiding data leakage through shared retrieval indexes

This is especially important if a system is used by more than one bank, more than one internal business unit, or more than one client group.

### Audit Logging

Audit logging preserves a record of access and important system actions.

Questions an audit log should help answer include:

- Who accessed a sensitive record?
- Which agent or service processed it?
- Which tool was called?
- What output was produced?
- Which rule or model version was used?
- Who approved, rejected, or overrode the result?

Audit logging supports security investigations, compliance reviews, and operational troubleshooting.

---

## 1.8 The Technical Reading Guide for This Book

The case studies later in this book use several technical formats. This section explains how to read them.

### Architecture Diagrams

Architecture diagrams show the major parts of a system, the boundaries between services, and how information moves from one component to another.

They answer questions such as:

- Where does a user request enter the system?
- Which component calls the LLM?
- Where are calculations performed?
- When does human review happen?
- Where are documents and audit records stored?

### Sequence Diagrams

Sequence diagrams show the order in which users, APIs, agents, tools, databases, and reviewers interact.

They help explain request lifecycle and orchestration.

For example:

```text
Analyst uploads document
  -> API accepts request
  -> job is created
  -> worker processes document
  -> extraction agent retrieves evidence
  -> validation service checks output
  -> calculation engine produces metrics
  -> analyst reviews the result
```

### Entity Relationship Diagrams

Entity relationship diagrams show how stored records connect to one another.

Examples include:

- Document and extracted fact
- Client and KYC assessment
- Borrower and credit rating
- Security and portfolio position
- Research note and evidence citation

These diagrams help readers understand how a system preserves lineage, history, review actions, and auditability.

### State Diagrams

State diagrams show how a document, job, assessment, or decision moves through a controlled process.

For example:

```text
Submitted
  -> Processing
  -> Validating
  -> Awaiting Review
  -> Approved
  -> Completed
```

They are useful because not every transition should be allowed. A case should not be marked complete if it still has an unresolved validation failure. A research note should not be published before the required reviewer approves it.

### API Tables

API tables describe endpoints, request fields, response fields, permissions, and error conditions.

They answer practical implementation questions such as:

- What request starts a document-processing job?
- What response does the caller receive?
- Which role is allowed to approve a case?
- How does the client check processing status?
- What error is returned when validation fails?

### Data and Schema Tables

Data and schema tables describe what the system stores, why it stores it, and how records relate to each other.

They help readers understand how the design supports reruns, review, historical comparison, and audit trails.

### Code Blocks

Code blocks show interfaces, formulas, rules, validation logic, and key implementation patterns. They are included to explain system design, not to turn the book into a framework-specific coding manual.

For example, a formula may show how a ratio is calculated, or a code block may show how an API validates a request before creating a job.

### Interview Conversations

The conversations between interviewer and candidate expose the reasoning behind every important design choice.

In the early part of each case study, the interviewer gives minimal details. The candidate must ask the questions needed to uncover the real business problem, stakeholders, success measures, data constraints, compliance requirements, and failure risks.

In the solution-design sections, the candidate begins to lead the discussion. The interviewer probes deeper:

- Why use an agent instead of a fixed workflow?
- Why is a task asynchronous?
- Why use a relational database?
- Why not let the LLM calculate the score?
- What happens when OCR fails?
- How is the output validated?
- How can the result be reproduced six months later?
- What happens when a reviewer overrides the system?

The goal is not to present one perfect answer. The goal is to show how an engineer, consultant, or architect reaches a clear and defensible answer.

---

## 1.9 Chapter Summary

This chapter introduced the technical foundation needed for the rest of the book.

The main ideas are:

- An LLM is not a complete banking system. It is one component inside a wider workflow.
- An AI agent can use approved tools and take several controlled steps toward a goal, but it needs limits, checks, and a clear stopping point.
- Workflows coordinate the process. Agents add controlled flexibility where the next step depends on what the system finds.
- Retrieval helps agents and LLMs work with relevant evidence rather than relying on memory or processing every document page at once.
- Document processing, OCR, and table extraction are essential when business data arrives in PDFs, scans, images, and complex layouts.
- Deterministic software should own calculations, scores, rankings, rules, constraints, validations, and workflow-state transitions.
- Data must be separated into original evidence, extracted facts, validated facts, derived metrics, decisions, and narratives.
- Versioning, data lineage, and audit records make a system explainable and reproducible.
- Long-running work needs background jobs, queues, workers, clear job states, retries, and safe rerun behavior.
- Human review, authorization, least-privilege access, and audit logging are part of the architecture from the start.

Chapter 2 moves from these technical building blocks to the banking business processes behind the four case studies: financial-document analysis, KYC and enhanced due diligence, corporate credit assessment, and investment research.
