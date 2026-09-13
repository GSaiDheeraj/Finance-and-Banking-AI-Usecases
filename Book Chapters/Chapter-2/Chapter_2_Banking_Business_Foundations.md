# Chapter 2: Banking Business Foundations

This chapter explains the business context behind the four case studies in this book. It is written for readers who understand technology but are new to banking, as well as banking professionals who want a clear view of how business workflows connect to agentic AI systems.

The chapter covers four areas:

1. Financial document analysis
2. KYC and enhanced due diligence
3. Corporate credit risk assessment
4. Investment research and portfolio intelligence

The purpose is not to teach every banking product, regulation, or business process. It is to explain the people, documents, decisions, risks, and business outcomes that shape the technical systems in the later chapters.

---

## 2.1 How Banking Functions Are Organized

Banks and financial institutions commonly organize work across three broad groups: the front office, the middle office, and control functions. Names vary across organizations, but this division is useful because it shows who serves clients, who measures and manages risk, and who independently checks that rules are followed.

These groups must work together. A relationship manager may want to onboard a valuable client quickly. Compliance must decide whether the client has been checked properly. A credit team may want to support a loan proposal. Risk teams need to decide whether the borrower can repay. An investment team may identify an attractive opportunity. Portfolio controls must ensure that an investment does not breach agreed limits.

The systems in this book sit at these points of interaction. Their role is to make work faster and more consistent while keeping the right people responsible for important decisions.

### 2.1.1 Front Office

The front office is the business-facing part of a bank or investment firm. It works directly with customers, borrowers, investors, products, and markets. Its main responsibilities are building relationships, serving clients, identifying opportunities, and generating revenue.

1. **Relationship managers**

   Relationship managers maintain and grow client relationships. In corporate banking, they may work with businesses that need loans, cash-management services, trade finance, foreign-exchange services, or other banking products. In retail and private banking, they may support individuals and families.

   Their role commonly includes:

   a. Understanding the client’s business or financial needs.
   b. Explaining relevant banking products and services.
   c. Gathering documents for loan applications or onboarding.
   d. Coordinating with credit, compliance, operations, and risk teams.
   e. Providing commercial context that may not be visible in a financial statement or form.

   A relationship manager is usually not the final owner of a credit or compliance decision. For example, they may explain that a borrower’s revenue fell because of a temporary factory shutdown, but the credit team still needs to independently assess whether the borrower can repay a loan.

2. **Private bankers**

   Private bankers work with high-net-worth individuals, families, and family offices. Their clients may have complex arrangements involving trusts, holding companies, foundations, inherited wealth, investment entities, and connections to multiple countries.

   Their role commonly includes:

   a. Understanding the client’s wealth-management and banking needs.
   b. Gathering documentation required to begin or continue a relationship.
   c. Explaining the bank’s services and onboarding requirements.
   d. Coordinating with compliance teams when enhanced due diligence is required.
   e. Maintaining the client relationship while respecting the bank’s review process.

   Private banking creates a clear tension between service and control. Clients expect a responsive experience, but complex structures often need deeper verification. The KYC/EDD case study shows how an AI system can help prepare a case without bypassing compliance approval.

3. **Investment analysts**

   Investment analysts study companies, industries, and markets. They use annual reports, financial statements, earnings releases, presentations, market data, news, and other evidence to understand a company and form a view.

   Their role commonly includes:

   a. Reading company and industry information.
   b. Extracting financial data from reports and filings.
   c. Comparing performance across periods and against peers.
   d. Calculating financial ratios and identifying trends.
   e. Identifying key risks, opportunities, and events that may affect the company.
   f. Preparing research notes, internal recommendations, or materials for investment decisions.

   The Financial Document Extraction Agent and Investment Research Copilot case studies support this work. Their purpose is to reduce repetitive reading and data-entry work, not to replace an analyst’s professional judgment.

4. **Sales and trading teams**

   Sales and trading teams help clients buy and sell financial products such as shares, bonds, currencies, interest-rate products, and other market instruments.

   Their role commonly includes:

   a. Understanding client demand and investment needs.
   b. Providing market information and product ideas.
   c. Supporting the purchase and sale of financial instruments.
   d. Managing market activity within approved limits.
   e. Maintaining records of communications, transactions, and approvals where required.

   The case studies in this book do not focus on trade execution. However, research, market data, portfolio constraints, and risk measures are closely connected to the information sales and trading teams use.

5. **Portfolio managers**

   Portfolio managers decide how money should be invested within an agreed objective, strategy, and set of limits. They may manage funds, institutional mandates, private-banking portfolios, pension portfolios, or other investment accounts.

   Their role commonly includes:

   a. Selecting investments to buy, hold, reduce, avoid, or sell.
   b. Deciding how much capital to allocate to each investment.
   c. Balancing potential return against risk.
   d. Monitoring exposure to companies, sectors, countries, and market factors.
   e. Ensuring decisions remain within the portfolio’s mandate and limits.
   f. Approving or overriding research recommendations when necessary.

   A portfolio manager can use AI-generated research support, but the portfolio manager remains accountable for investment decisions and exceptions.

### 2.1.2 Middle Office

The middle office supports business activity by measuring risk, checking information, coordinating processes, and ensuring that decisions can be supported properly. It often sits between revenue-generating teams and independent control functions.

1. **Credit risk**

   Credit risk is the risk that a borrower will not repay money owed to the bank. The borrower may be an individual, a company, a government, or another financial institution.

   Credit-risk teams are responsible for:

   a. Reviewing the borrower’s ability to repay.
   b. Assessing financial strength, cash flow, debt, and collateral.
   c. Evaluating industry conditions and other risks.
   d. Assigning or reviewing an internal credit rating.
   e. Monitoring credit exposure after a loan is approved.
   f. Escalating cases that exceed delegated authority or policy limits.

   The Corporate Credit Risk Assessment case study focuses on how a system can support this work through document extraction, calculations, scorecards, and evidence-based credit memos.

2. **Market risk**

   Market risk is the risk of loss caused by changes in market prices or conditions. These changes can involve interest rates, exchange rates, share prices, bond prices, commodity prices, or credit conditions.

   Market-risk teams are responsible for:

   a. Measuring how market movements could affect portfolios and positions.
   b. Monitoring exposure against approved risk limits.
   c. Running scenarios to understand the impact of difficult market conditions.
   d. Reporting material risks to management and relevant committees.
   e. Challenging positions or strategies that create unacceptable exposure.

   The Investment Research Copilot case study does not replace market-risk management. It shows why investment systems must calculate exposures and constraints clearly before a recommendation reaches a portfolio manager.

3. **Compliance**

   Compliance teams help ensure that the organization follows laws, regulations, internal policies, and conduct standards.

   Compliance responsibilities commonly include:

   a. Customer onboarding and financial-crime prevention.
   b. Sanctions screening and related checks.
   c. Monitoring for unusual or concerning activity.
   d. Ensuring required approvals and documentation are present.
   e. Advising business teams on policy requirements.
   f. Reporting issues or concerns to management and regulators where required.

   Compliance is not simply an approval button added at the end of a workflow. It influences how the workflow is designed from the beginning, including what evidence is collected, which checks are mandatory, and who may approve a case.

4. **KYC and EDD**

   KYC means Know Your Customer. It is the process of confirming who a customer is and understanding enough about the customer to manage risk appropriately.

   EDD means Enhanced Due Diligence. It is a more detailed process used for complex or higher-risk relationships.

   KYC and EDD teams are responsible for:

   a. Verifying customer identity.
   b. Understanding ownership and control of legal entities.
   c. Identifying ultimate beneficial owners.
   d. Reviewing source of wealth and source of funds.
   e. Screening relevant people and entities against approved sources.
   f. Documenting risk factors and open questions.
   g. Escalating high-risk or unclear cases for senior approval.

   The KYC/EDD Risk Scoring Agent case study focuses on improving evidence gathering, document reading, ownership analysis, and case preparation while preserving compliance accountability.

5. **Operations**

   Operations teams support the day-to-day running of the bank. Their responsibilities may include account setup, document handling, payments, settlement support, reconciliation, customer-service administration, and exception management.

   Operations teams commonly need to:

   a. Process large volumes of documents and requests.
   b. Check whether required information is complete.
   c. Enter or verify data in operational systems.
   d. Match information across systems and resolve differences.
   e. Follow up on missing documents or incomplete cases.
   f. Escalate exceptions that cannot be handled through standard procedures.

   Operations work often contains highly repetitive tasks. This creates opportunities for automation, but the system must be designed to handle exceptions safely and visibly.

6. **Model risk management**

   Model risk management oversees the use of models that influence business decisions. A model may be a traditional statistical model, a machine-learning model, a fraud model, a credit model, or an AI system that contributes information to a decision.

   Model risk management asks questions such as:

   a. What business problem does the model support?
   b. What data does it use?
   c. What are its limitations?
   d. How is performance tested and monitored?
   e. Who owns the model and who approves changes?
   f. Can the output be explained?
   g. What happens when the model is unavailable or produces a poor result?
   h. Does the model operate only within its intended use?

   These questions matter for agentic AI systems even when the LLM is not making the final decision. The organization still needs to understand how the model affects document extraction, classification, retrieval, summaries, and recommendations.

### 2.1.3 Control Functions

Control functions provide independent oversight. Their role is to ensure that commercial urgency does not override legal duties, risk limits, data protection, security standards, or internal policies.

1. **Compliance officers**

   Compliance officers review whether business activity follows applicable requirements. In a client-onboarding process, a compliance officer may request more information, approve a case with conditions, reject a case, or escalate it for more senior review.

   A compliance officer needs access to:

   a. Client and entity information collected during onboarding.
   b. Documents and sources used to support the case.
   c. Ownership and control relationships.
   d. Screening results and identity-match confidence.
   e. Applied risk factors and scoring rules.
   f. Open questions, failed validations, and policy exceptions.
   g. The recommendation and the reason for escalation.

   A simple AI-generated summary is not enough. The reviewer must be able to inspect the evidence behind it.

2. **MLRO**

   MLRO stands for Money Laundering Reporting Officer. The MLRO is a senior person responsible for overseeing the organization’s approach to money-laundering risk.

   Cases may be escalated to an MLRO when they involve:

   a. Complex ownership structures.
   b. High-risk country connections.
   c. Political exposure.
   d. Negative information that cannot be resolved easily.
   e. Unusual or unclear source-of-wealth evidence.
   f. Significant financial-crime concerns.
   g. High-value or high-profile relationships.

   The MLRO needs a complete case record, not only a final score. The system must preserve documents, screening results, risk factors, reviewer comments, and decision history.

3. **Internal audit**

   Internal audit independently checks whether the organization’s controls are designed properly and working in practice.

   Internal audit may ask:

   a. Did the correct person approve this decision?
   b. Were required checks completed before approval?
   c. Can the organization show the documents and calculations behind the result?
   d. Which model, rule, prompt, or reference-data version was used?
   e. Did the system allow a case to progress despite a failed validation?
   f. Are overrides recorded and reviewed?
   g. Can the organization reproduce an earlier result?

   This is why later chapters discuss audit logs, data lineage, versioning, approval records, and state-machine design.

4. **Legal**

   Legal teams advise on contracts, regulatory obligations, disputes, privacy, intellectual property, and the legal implications of business decisions.

   In an AI-related workflow, legal teams may consider:

   a. Whether the organization may use a specific data source.
   b. Whether a third-party model provider is acceptable.
   c. Where sensitive data may be stored or processed.
   d. How long records should be retained.
   e. Whether a customer must receive a specific disclosure.
   f. Whether an automated output can affect a customer decision.
   g. How evidence should be preserved for disputes or investigations.

   The case studies are not legal advice. They show why legal requirements must be considered before choosing models, tools, data stores, and workflow designs.

5. **Information security**

   Information-security teams protect systems and data from unauthorized access, misuse, loss, and disruption.

   For an agentic AI system, information security may ask:

   a. Who can access customer documents and internal research?
   b. Which tools can the AI agent call?
   c. Can the agent access information outside the assigned case?
   d. Are credentials and secrets protected?
   e. Is sensitive information being sent to an approved model provider?
   f. Are external integrations secured and monitored?
   g. Can the organization investigate suspicious access or actions later?

   These concerns shape access control, tool permissions, data isolation, logging, and deployment design.

6. **Data governance**

   Data governance is the discipline of ensuring that important data is accurate, available, understood, protected, and used responsibly.

   Data-governance teams care about:

   a. Data ownership.
   b. Common definitions for important terms and measures.
   c. Data quality checks.
   d. Data lineage and source traceability.
   e. Retention and deletion rules.
   f. Access controls.
   g. Change management.
   h. Data versioning.

   A well-designed system distinguishes between a raw document, an extracted value, a validated value, a calculated metric, and an approved decision. If these are mixed together, it becomes difficult to understand what the system knows, what it inferred, and what a human approved.

---

## 2.2 Financial Document Analysis in Banking and Investment Research

Financial document analysis is the process of reading company documents and turning them into information that analysts, credit teams, investment teams, and decision-makers can use.

A company can produce annual reports, quarterly reports, financial statements, investor presentations, credit agreements, and regulatory filings. These documents contain valuable information, but they are often long, inconsistent in format, and spread across several locations.

### 2.2.1 What Financial Documents Contain

Financial documents serve different business purposes. A good analyst understands what each document can reveal and where its limitations are.

1. **Annual reports**

   An annual report is a detailed report on a company’s performance over a full financial year. It often includes financial statements, management commentary, business strategy, risks, governance information, and notes explaining the numbers.

   Annual reports commonly contain:

   a. A broad overview of the business and its strategy.
   b. Audited financial statements.
   c. Management discussion of results and outlook.
   d. Descriptions of key business risks.
   e. Notes on debt, leases, legal matters, acquisitions, accounting changes, and related-party transactions.
   f. Information about directors, governance, and shareholder matters.

   Important information may appear in a footnote or appendix rather than in the main financial statements.

2. **Quarterly reports**

   Quarterly reports provide performance information for a shorter period, often three months. They help analysts understand recent changes after the last annual report.

   Quarterly reports may contain:

   a. Revenue and profit updates.
   b. Recent cash-flow and balance-sheet changes.
   c. Management guidance.
   d. Segment performance.
   e. Material events since the prior report.
   f. Updates on risks or litigation.

   They are useful for timely analysis but may be less detailed than annual reports.

3. **Financial statements**

   Financial statements are formal records of a company’s financial position and performance.

   The main statements are:

   a. Income statement: Shows revenue, expenses, profit, and loss during a period.
   b. Balance sheet: Shows what the company owns, what it owes, and the value belonging to shareholders at a point in time.
   c. Cash flow statement: Shows how cash moved into and out of the company.
   d. Statement of changes in equity: Shows changes in the owners’ interest in the company.

   The notes to the statements often explain the numbers. For example, a debt number on the balance sheet may require a note to understand repayment timing, interest rates, collateral, or covenants.

4. **Earnings presentations**

   An earnings presentation is a shorter presentation released around an earnings announcement. It often highlights management’s preferred view of performance, selected operating measures, recent achievements, and outlook.

   Earnings presentations often include:

   a. Key financial highlights.
   b. Business-unit or segment performance.
   c. Management guidance.
   d. Selected operating measures.
   e. Charts showing trends.
   f. Commentary on strategic priorities.

   They are useful for speed, but analysts should compare them with detailed financial statements and disclosures because they may use adjusted measures or selective comparisons.

5. **Investor decks**

   Investor decks are presentations prepared for investors, lenders, or other stakeholders. They explain the company’s business model, market position, strategy, operating metrics, growth plans, and future opportunities.

   Investor decks may help an analyst understand:

   a. How the company describes its business.
   b. Which markets and customer segments it serves.
   c. What management considers its competitive advantages.
   d. Which metrics management emphasizes.
   e. What future plans or targets are being communicated.

   A system should distinguish between historical facts and forward-looking claims. A management target is not the same as a reported result.

6. **Credit agreements**

   Credit agreements describe the terms under which a company borrows money. They may include interest rates, repayment schedules, collateral, financial covenants, reporting requirements, and events that allow the lender to take action.

   Credit agreements can reveal:

   a. How much debt the company has agreed to take.
   b. When repayment is due.
   c. Whether debt is secured by assets.
   d. Which financial conditions the company must maintain.
   e. What happens if the borrower breaches an agreement.
   f. Which lenders have priority in repayment.

7. **Regulatory filings**

   Regulatory filings are documents submitted to market regulators, stock exchanges, or other authorities. They may include annual and quarterly filings, material-event notices, ownership disclosures, prospectuses, and other required reports.

   They are useful because they often provide:

   a. Official versions of disclosed information.
   b. Filing dates and entity identifiers.
   c. Formal risk disclosures.
   d. Information about material events.
   e. Ownership and governance disclosures.
   f. A record that can be cited and verified later.

### 2.2.2 How Analysts Use Them

Analysts use financial documents to understand performance, financial condition, risk, and future possibilities. They do not simply collect numbers. They use the information to form a view and support a recommendation.

1. **Financial statement analysis**

   Financial statement analysis means reviewing the income statement, balance sheet, cash flow statement, and notes to understand a company’s financial condition.

   An analyst may examine:

   a. Whether revenue is growing or declining.
   b. Whether profits are improving at the same pace as revenue.
   c. Whether the company generates cash or consumes cash.
   d. Whether debt is increasing.
   e. Whether there are unusual one-time gains or losses.
   f. Whether accounting policies changed.
   g. Whether a small number of customers, suppliers, or markets create dependence.
   h. Whether management restated earlier results.

2. **Ratio calculation**

   Financial ratios turn raw values into measures that are easier to compare across periods or companies.

   Common ratios include:

   a. Revenue growth: Shows how sales changed over time.
   b. Operating margin: Shows the operating profit earned from each unit of revenue.
   c. Debt-to-equity: Compares debt with the shareholders’ capital supporting the business.
   d. Interest coverage: Shows how comfortably earnings can cover interest costs.
   e. Free cash flow: Shows cash remaining after required investment in the business.
   f. Liquidity measures: Show whether the company can meet short-term obligations.

   An LLM can help find the figures. However, the system should calculate ratios using regular, repeatable software and validated inputs.

3. **Company comparison**

   Analysts compare companies with similar businesses, industries, markets, or risk profiles.

   A comparison may consider:

   a. Growth rates.
   b. Profitability.
   c. Debt levels.
   d. Cash generation.
   e. Valuation.
   f. Market position.
   g. Customer concentration.
   h. Management quality.

   Comparison requires care because companies may report in different currencies, use different financial year ends, define adjusted measures differently, or operate under different business conditions.

4. **Trend analysis**

   Trend analysis looks at changes across several periods rather than only one year or quarter.

   Analysts may look for:

   a. Consistent revenue growth or decline.
   b. Improving or deteriorating margins.
   c. Rising debt.
   d. Changes in cash generation.
   e. Changes in working capital.
   f. Repeated restructuring costs.
   g. Increasing legal or regulatory risk.
   h. Signs that a business model is strengthening or weakening.

   A multi-year view can reveal patterns that are not obvious from a single report.

5. **Risk identification**

   Financial documents often contain warning signs outside the main financial statements.

   Analysts may look for:

   a. Going-concern language.
   b. Major legal disputes.
   c. Breaches of financial covenants.
   d. Large related-party transactions.
   e. Customer or supplier concentration.
   f. Significant changes in accounting policy.
   g. Rapid debt growth.
   h. Currency, interest-rate, or commodity-price exposure.

   These issues are often buried in notes or risk sections. A document-analysis system should help surface them as structured evidence rather than hide them in a long summary.

6. **Research-note preparation**

   A research note is a written analysis of a company, industry, security, or investment idea. It may include business overview, financial performance, valuation, key risks, catalysts, and an investment view.

   A controlled research-note process should:

   a. Gather and validate source information.
   b. Calculate required metrics consistently.
   c. Identify relevant risks and changes.
   d. Draft a narrative based on validated facts.
   e. Review the draft for accuracy and completeness.
   f. Obtain supervisory approval where required.

   AI can help with drafting, but it should not invent facts or bypass review.

7. **Credit-memo preparation**

   A credit memo supports a lending or credit decision. It explains the borrower, financial position, repayment ability, risks, proposed terms, and recommendation.

   A credit memo often requires:

   a. Borrower background.
   b. Financial-statement analysis.
   c. Debt and cash-flow analysis.
   d. Industry and management assessment.
   e. Collateral and loan-structure information.
   f. Covenant analysis.
   g. Risk rating and key rating drivers.
   h. Recommendation and requested approval.

### 2.2.3 Business Pain Points

Financial document analysis is valuable, but it is time-consuming and vulnerable to avoidable errors.

1. **Hundreds of pages per company**

   A large company may issue an annual report, interim report, earnings release, presentation, and other filings in one year. An analyst covering many companies may need to work through thousands of pages during a reporting season.

   The challenge is not just volume. Important information may be spread across several documents and hidden in notes, appendices, or charts.

2. **Manual extraction into spreadsheets**

   Analysts often find values in reports and type them into spreadsheets or internal systems. This work is repetitive and creates opportunities for mistakes.

   Common mistakes include:

   a. Copying a value from the wrong column.
   b. Using a prior year instead of the current year.
   c. Missing a unit such as thousands or millions.
   d. Mixing adjusted and reported values.
   e. Assigning a value to the wrong company or reporting period.
   f. Forgetting where a value came from.

3. **Restatements and inconsistent presentation**

   Companies sometimes revise earlier financial figures. A later report may show a corrected prior-year value that differs from the original filing.

   Companies also use different labels and formats for similar concepts. One company may report “revenue,” another “net sales,” and another “turnover.” A system needs clear rules for storing original labels, normalized labels, periods, units, currencies, and source documents.

4. **Time pressure after earnings releases**

   When a company releases results, analysts may need to update models, prepare notes, speak with clients, and brief decision-makers quickly.

   The business needs speed, but speed without validation can spread incorrect data. The goal is faster preparation with visible evidence and checks.

5. **Lack of source traceability**

   A spreadsheet may contain a number without recording the exact document, page, date, and source label. This becomes a problem when a reviewer asks why a number was used or when an analyst revisits the work months later.

   Traceability means that an important number can be linked back to the original source and the steps that used it.

6. **Analyst capacity constraints**

   Analysts have limited time. As the number of companies, documents, or reporting periods grows, manual work can consume the time needed for real judgment.

   The business value of an AI system is not just faster extraction. It is giving analysts more time for comparison, interpretation, risk assessment, and communication.

### 2.2.4 What a Good Outcome Looks Like

A good financial-document analysis system improves speed, consistency, and traceability without claiming to replace expert judgment.

1. **Faster research cycles**

   The system should reduce the time between a document becoming available and an analyst receiving a useful, organized set of information.

2. **Reduced manual transcription**

   The system should reduce repetitive copying of values from documents into spreadsheets and internal tools. It should not create a new burden where analysts spend all their saved time correcting unreliable output.

3. **Traceable numbers**

   Every important value should be traceable to a source document, page, label, period, and unit. A calculated metric should show the inputs and formula used.

4. **Better review workflows**

   The system should clearly show uncertainty. If a table could not be read, a value conflicts with another source, or evidence is missing, the system should flag the issue for review.

5. **More analyst time for judgment and thesis development**

   The best outcome is a better analyst workflow: less time spent on manual collection and transcription, and more time spent understanding what the information means.

---

## 2.3 KYC, EDD, and Client Onboarding

Before a bank begins or continues a relationship with a customer, it needs to understand who the customer is and whether the relationship creates an acceptable level of risk.

This is the purpose of KYC, or Know Your Customer. When the relationship is more complex or higher risk, the bank performs EDD, or Enhanced Due Diligence.

### 2.3.1 What KYC Means

KYC is not only about checking a passport or confirming a name. It is about building a reasonable understanding of the customer, the people behind the customer, the purpose of the relationship, and the financial-crime risks that may need further review.

1. **Verify who the client is**

   The bank needs to confirm the customer’s identity.

   a. For an individual, this may include:

   - Identity documents
   - Proof of address
   - Date of birth
   - Nationality or residency details
   - Tax or regulatory information where required

   b. For a company, this may include:

   - Incorporation documents
   - Registered address
   - Directors and authorized signatories
   - Shareholder information
   - Business activity and purpose

   Identity verification is the starting point. It does not by itself explain who controls the entity, where wealth came from, or whether financial-crime concerns exist.

2. **Understand ownership and control**

   A customer may be a company, trust, foundation, partnership, or another legal structure. The direct account holder may not be the person who ultimately owns, controls, or benefits from the assets.

   The bank needs to understand:

   a. Which people and entities are involved.
   b. Who owns each entity.
   c. Who controls decision-making.
   d. Whether anyone has significant influence without formal ownership.
   e. How the structure connects to the customer relationship.

3. **Identify the ultimate beneficial owner**

   The ultimate beneficial owner, often called the UBO, is the person who ultimately owns or controls an entity or benefits from it, even if their name does not appear as the direct account holder.

   For example:

   ```text
   Person A
     -> owns Holding Company 1
     -> which owns Operating Company 2
     -> which is opening an account
   ```

   The account may be in the name of Operating Company 2, but the bank still needs to understand Person A’s ownership or control.

4. **Check source of wealth and source of funds**

   Source of wealth explains how a person became wealthy over time. Examples include salary, business ownership, inheritance, investment gains, property sale, or sale of a company.

   Source of funds explains where the money used for a particular account, investment, or transaction came from.

   The bank needs to assess:

   a. Whether the explanation is plausible.
   b. Whether it fits the customer’s known profile.
   c. Whether documents support the explanation.
   d. Whether there are unexplained gaps or inconsistencies.
   e. Whether the intended account activity fits the stated purpose.

5. **Screen for sanctions, PEP exposure, and adverse media**

   Banks screen customers and connected parties against approved sources.

   a. **Sanctions** are restrictions imposed by governments or international bodies on certain people, entities, countries, or activities.
   b. **PEP exposure** refers to politically exposed persons, people who hold or have held prominent public roles. Political exposure does not mean wrongdoing, but it can require additional scrutiny.
   c. **Adverse media** refers to credible negative public information that may indicate fraud, corruption, financial crime, legal disputes, or other concerns relevant to the relationship.

   A screening match is not automatically a conclusion. Names can be common, articles can be outdated, and the information may concern a different person. A reviewer must assess identity match, relevance, reliability, and materiality.

### 2.3.2 What Enhanced Due Diligence Means

EDD is a deeper review used when ordinary onboarding checks are not sufficient.

1. **More evidence required**

   A higher-risk case may require more documents, independent records, detailed explanations, and stronger evidence.

   Examples may include:

   a. Corporate records for several connected companies.
   b. Trust deeds and foundation documents.
   c. Ownership records.
   d. Bank statements.
   e. Tax records or business-sale documents.
   f. Evidence supporting source of wealth and source of funds.
   g. Additional information about the purpose of the relationship.

2. **More detailed source-of-wealth checks**

   EDD may require the bank to look more deeply at whether a customer’s stated wealth source fits the available evidence.

   For example, if a client says their wealth came from selling a business, the bank may review:

   a. Evidence of ownership before the sale.
   b. Documents showing the sale took place.
   c. The amount received from the sale.
   d. The route by which proceeds reached the client.
   e. Public information about the business and transaction where available.

3. **Additional approval layers**

   Higher-risk cases often require approval by a compliance officer or a senior person such as the MLRO.

   The approval process should make clear:

   a. Who reviewed the case.
   b. What evidence they considered.
   c. What risk factors were present.
   d. What conditions, if any, were attached to approval.
   e. Why a decision was made.

4. **Ongoing monitoring**

   Customer risk can change after onboarding.

   Ongoing monitoring may consider:

   a. Changes in ownership or control.
   b. New political exposure.
   c. New negative information.
   d. Changes in country connections.
   e. Unusual activity compared with the stated account purpose.
   f. Periodic review dates.

5. **Escalation to a compliance officer or MLRO**

   A case should be escalated when evidence is incomplete, risk is high, or concerns cannot be resolved at the analyst level.

   A useful escalation package contains:

   a. Client and entity information.
   b. Key documents and evidence.
   c. Ownership and control diagram.
   d. Screening results.
   e. Risk factors and score.
   f. Open questions and validation failures.
   g. The reason escalation is required.
   h. The recommended next step.

### 2.3.3 Common Complexities

Real customer structures can be much more complicated than a single person opening one account.

1. **Trusts**

   A trust is a legal arrangement where assets are held and managed by one party for the benefit of another. It may involve a settlor, trustees, protectors, beneficiaries, and other parties.

   The bank may need to understand:

   a. Who created the trust.
   b. Who manages the trust.
   c. Who benefits from the trust.
   d. Who can change trustees or beneficiaries.
   e. Who has real control over key decisions.

2. **Foundations**

   A foundation is a legal structure that can hold assets for a stated purpose or group of beneficiaries. Different countries treat foundations differently.

   The bank may need to identify:

   a. Founder or founders.
   b. Council members or directors.
   c. Beneficiaries.
   d. Protectors or supervisory persons.
   e. People with decision-making authority.

3. **Holding companies**

   A holding company owns shares in other companies or assets rather than operating a normal business itself.

   Holding companies can be legitimate structures for investment, ownership, family wealth, or business organization. They can also create multiple layers that make beneficial ownership harder to understand.

4. **Offshore structures**

   An offshore structure involves an entity, account, trust, or arrangement in a country different from where the owner lives or operates.

   Offshore does not automatically mean wrongdoing. However, it can increase complexity and may require additional review depending on the countries involved, the purpose of the structure, and how transparent ownership information is.

5. **Family offices**

   A family office manages financial, investment, legal, tax, or administrative matters for a wealthy family.

   A family office relationship may include several people, companies, trusts, foundations, and investment vehicles. The bank needs to understand who makes decisions, who benefits, and who is relevant to the risk assessment.

6. **Multiple jurisdictions**

   A client may live in one country, earn wealth in another, own companies in several countries, and open an account in a different country.

   Multiple jurisdictions can create complexity because of:

   a. Different document standards.
   b. Different legal structures.
   c. Different risk classifications.
   d. Different languages.
   e. Different information availability.
   f. Different regulatory requirements.

7. **Ownership diagrams**

   Ownership diagrams are visual charts showing people, entities, trusts, relationships, and ownership percentages.

   They can be hard to process automatically because the system needs to understand not just the names on the chart but also the direction of ownership and which percentage belongs to which relationship.

8. **Same-name individuals**

   A search may return negative information about a person with the same name as a client. This does not mean the information applies to the client.

   The system and reviewer need to consider supporting details such as:

   a. Date of birth.
   b. Nationality.
   c. Country of residence.
   d. Employer or company affiliation.
   e. Known associates.
   f. Location.
   g. Identity-document details where appropriate.

   If the identity match is uncertain, the system should state that clearly and route the result to review.

### 2.3.4 Business Consequences of Errors

KYC and EDD mistakes can harm the bank and the customer.

1. **Regulatory breaches**

   A bank may face regulatory action if it fails to perform required checks, ignores warning signs, or cannot show that it followed its process.

2. **Sanctions exposure**

   Serving a sanctioned person, entity, or activity can create severe legal, financial, and operational consequences. Screening must be accurate, current, and reviewed appropriately.

3. **Reputational damage**

   A bank can lose trust if it becomes associated with corruption, money laundering, fraud, or other financial-crime concerns.

4. **Financial crime risk**

   Poor onboarding can allow accounts or services to be used for money laundering, fraud, bribery, tax crime, or other illegal activity.

5. **Poor customer experience from false positives**

   A false positive occurs when a system flags a legitimate client or innocent information as suspicious. Too many false positives can delay onboarding, frustrate clients, and overload compliance teams.

6. **Slow onboarding and lost business**

   A legitimate client may choose another institution if onboarding is slow, unclear, or repetitive. The business therefore needs controlled speed: faster evidence collection and case preparation without reducing the quality of review.

---

## 2.4 Corporate Credit Risk Assessment

Banks lend money to companies for working capital, expansion, equipment purchases, acquisitions, trade finance, and other needs. Before approving a loan, the bank needs to understand whether the borrower can and is likely to repay.

This process is called corporate credit assessment.

### 2.4.1 What Corporate Credit Assessment Means

Corporate credit assessment evaluates a company’s ability and willingness to repay debt under agreed terms.

1. **Assess a borrower’s ability and willingness to repay**

   Ability to repay concerns the company’s financial capacity. The bank asks whether the borrower has adequate cash flow, manageable debt, and the ability to continue operating under reasonable stress.

   Willingness to repay is harder to observe directly. It may be informed by:

   a. Payment history.
   b. Management behavior.
   c. Governance quality.
   d. Past compliance with loan terms.
   e. Legal or regulatory history.
   f. Relationship with lenders.

2. **Review financial strength**

   Financial strength includes profitability, balance-sheet quality, cash flow, liquidity, capital structure, and access to funding.

   A company may have high revenue but still be financially weak if it has:

   a. Very low margins.
   b. Weak cash collection.
   c. Excessive debt.
   d. Large short-term repayments.
   e. Dependence on one customer.
   f. Unstable earnings.
   g. Limited access to refinancing.

3. **Analyze cash flow and leverage**

   Cash flow matters because loans are repaid with cash, not accounting profit alone.

   Leverage refers to the amount of debt a company has relative to its earnings, cash flow, or equity. Debt can help fund growth, but high leverage increases risk when earnings fall, interest rates rise, or refinancing becomes difficult.

4. **Evaluate industry and management risks**

   Financial statements are important, but they are not the complete picture.

   A credit assessment also considers:

   a. Industry demand and competition.
   b. Regulatory changes.
   c. Supply-chain dependence.
   d. Commodity-price or currency exposure.
   e. Customer concentration.
   f. Management experience.
   g. Governance quality.
   h. Acquisition strategy.
   i. Succession planning.

5. **Assign a risk rating**

   A risk rating is an internal classification of the borrower’s credit quality. The scale differs by institution, but it generally ranges from strong credit quality to high risk or default.

   A rating supports:

   a. Consistent lending decisions.
   b. Appropriate loan pricing.
   c. Approval-authority decisions.
   d. Portfolio monitoring.
   e. Expected-loss measurement.
   f. Reporting to management and regulators.

6. **Recommend lending terms and limits**

   A credit assessment may recommend more than approve or reject. It may suggest:

   a. How much the bank should lend.
   b. How long the facility should run.
   c. What pricing should apply.
   d. What collateral may be needed.
   e. Which financial covenants should be included.
   f. What reporting the borrower must provide.
   g. Whether the borrower requires closer monitoring.

### 2.4.2 Typical Stakeholders

Credit assessment requires commercial understanding, financial analysis, independent risk review, and delegated approval.

1. **Relationship manager**

   The relationship manager originates the lending opportunity, gathers initial information, explains the customer relationship, and works with the borrower on proposed terms.

2. **Credit analyst**

   The credit analyst reviews financial information, calculates ratios, assesses repayment ability, compares the borrower with peers, identifies risks, and prepares the credit memo.

3. **Underwriter**

   The underwriter assesses whether the proposed lending structure is appropriate. The exact role varies across institutions, but it generally involves evaluating risk before the bank commits capital.

4. **Credit risk manager**

   A credit risk manager oversees credit quality and consistency. They may challenge assumptions, review larger cases, monitor policy compliance, and assess exposure across the portfolio.

5. **Credit committee**

   A credit committee is a group of authorized decision-makers that reviews significant or higher-risk credit proposals.

   The committee may:

   a. Approve the proposal.
   b. Reject the proposal.
   c. Request more analysis.
   d. Approve subject to conditions.
   e. Reduce the amount or change terms.
   f. Require more senior approval.

6. **Portfolio-risk team**

   The portfolio-risk team looks across the bank’s lending book. It considers whether the bank has too much exposure to one industry, region, customer group, or risk type.

### 2.4.3 Core Financial Concepts

The following concepts appear regularly in corporate credit assessment.

1. **Revenue growth**

   Revenue is the money a company earns from selling goods or services. Revenue growth measures how that amount changes over time.

   Growth is not automatically a sign of strength. A company may grow by cutting prices, making acquisitions, extending payment terms, or taking on risky customers. Analysts consider growth alongside profits, cash flow, and debt.

2. **EBITDA**

   EBITDA stands for earnings before interest, taxes, depreciation, and amortization.

   In simple terms, it is often used as an indicator of earnings from the core business before financing costs, tax effects, and certain accounting charges.

   It can be useful for comparison, but it is not the same as cash flow and should not be treated as a complete measure of financial health.

3. **Operating margin**

   Operating margin shows how much operating profit a company makes from each unit of revenue after operating costs are considered.

   For example, if a company earns 100 in revenue and makes 15 in operating profit, its operating margin is 15%.

   A falling margin may indicate rising costs, lower pricing power, competitive pressure, or operational weakness.

4. **Debt-to-equity**

   Debt-to-equity compares a company’s debt with the capital provided by shareholders.

   A higher figure generally indicates greater reliance on debt. Debt can help fund growth, but it can also increase risk during downturns or periods of rising borrowing costs.

5. **Interest coverage**

   Interest coverage indicates how comfortably a company can pay interest costs from its earnings.

   A higher ratio generally means more room to service interest payments. A low ratio may indicate that even a modest decline in earnings could create repayment stress.

6. **Liquidity**

   Liquidity is the ability to meet short-term obligations when they fall due.

   A profitable company can still have liquidity trouble if it cannot collect cash from customers quickly enough, faces large near-term debt repayments, or owns assets that cannot easily be converted into cash.

7. **Free cash flow**

   Free cash flow is the cash remaining after a company pays for the investment needed to maintain or grow the business.

   It is important because it may be available for debt repayment, dividends, acquisitions, or other uses.

8. **Debt-service coverage**

   Debt-service coverage measures whether cash flow is sufficient to cover required debt payments, including interest and principal where relevant.

   It is especially important where repayment depends on predictable cash flows, such as project finance, real estate, infrastructure, or asset-backed lending.

9. **Covenant compliance**

   A covenant is a condition included in a loan agreement. It may require the borrower to maintain certain financial ratios, limit additional borrowing, provide regular reports, or avoid certain actions without lender consent.

   Covenant compliance means checking whether the borrower meets those agreed conditions.

### 2.4.4 Business Consequences of Errors

Credit assessment errors can affect profitability, capital, client relationships, and the bank’s reputation.

1. **Credit losses**

   If a borrower cannot repay, the bank may lose part or all of the money lent. Collateral may reduce the loss but may not fully protect the bank, especially when asset values fall.

2. **Poor pricing**

   If the bank underestimates risk, it may charge too little for the risk it takes. If it overestimates risk, it may lose strong borrowers to competitors.

3. **Inadequate loan-loss provisioning**

   Banks set aside money for expected credit losses. If risk is measured poorly, the bank may not reserve enough for potential losses.

4. **Concentration risk**

   A bank may have many loans that appear acceptable individually but are exposed to the same economic problem, such as one industry, region, supply chain, or group of related companies.

5. **Regulatory findings**

   Regulators expect banks to use clear credit policies, consistent rating processes, appropriate approvals, and evidence supporting major credit decisions.

6. **Inconsistent internal ratings**

   If similar borrowers receive very different ratings without a clear reason, the bank may price risk inconsistently and mismanage its loan portfolio.

---

## 2.5 Investment Research and Portfolio Intelligence

Investment research and portfolio intelligence help investment teams understand companies, markets, risks, and opportunities. The work may occur in an asset manager, wealth manager, private bank, pension fund, hedge fund, brokerage, or internal investment team.

The purpose is not to predict the future with certainty. It is to improve the quality, speed, consistency, and documentation of investment decisions.

### 2.5.1 What Investment Research Teams Do

Investment research teams gather evidence and develop a view about the potential risks and rewards of an investment.

1. **Analyze companies and sectors**

   Analysts study a company’s business model, financial results, management, competition, customers, suppliers, debt, valuation, and risks.

   They also study sectors and broader conditions, including:

   a. Demand trends.
   b. Regulation.
   c. Technology changes.
   d. Interest rates.
   e. Commodity prices.
   f. Currency movements.
   g. Supply-chain conditions.
   h. Competitive dynamics.

2. **Build investment theses**

   An investment thesis is a clear explanation of why an investor might own, avoid, buy, sell, or monitor a security.

   A strong thesis usually states:

   a. What the market may be missing.
   b. Why the company or asset may perform differently from expectations.
   c. What events could support the view.
   d. What risks could invalidate the view.
   e. What evidence the analyst is relying on.

   An AI system can organize evidence and draft a summary, but an investment thesis remains a professional judgment.

3. **Compare securities**

   A security is a financial asset such as a share or bond. Investment teams compare securities to decide which may offer better value, quality, growth, income, or risk-adjusted return.

   Comparison may include:

   a. Revenue growth.
   b. Profitability.
   c. Debt levels.
   d. Cash generation.
   e. Valuation.
   f. Dividend policy.
   g. Management quality.
   h. Price performance.
   i. Liquidity.
   j. Key risks.

4. **Identify catalysts and risks**

   A catalyst is an event that may cause the market to re-evaluate a company or security.

   Examples include:

   a. New product launches.
   b. Earnings improvement.
   c. Cost reduction.
   d. Regulatory approval.
   e. Asset sales.
   f. Industry recovery.
   g. Management change.

   Risks may include declining demand, rising costs, debt pressure, litigation, regulation, competition, poor execution, or a broader market downturn.

5. **Create research reports**

   Research reports explain an analyst’s view in a structured form. They may cover company background, financial analysis, valuation, catalysts, risks, and recommendation.

   The report must be based on valid evidence, consistent with underlying data, and reviewed according to the organization’s policies.

6. **Monitor market and company changes**

   Research continues after a report is published. Analysts monitor new filings, earnings releases, management announcements, price movements, macroeconomic developments, and industry events.

   A research system can help surface important changes. It should avoid overwhelming users with low-value alerts or presenting weak signals as confirmed conclusions.

### 2.5.2 What Portfolio Managers Do

Portfolio managers turn research and market information into investment decisions within a defined mandate.

A mandate is the agreed objective, strategy, and set of limits for a portfolio. It may specify target return, acceptable risk, asset types, countries, sectors, liquidity requirements, benchmark, and restrictions.

1. **Select securities**

   Portfolio managers decide which securities to buy, hold, reduce, avoid, or sell.

   They may use:

   a. Internal research.
   b. External research.
   c. Quantitative signals.
   d. Client requirements.
   e. Risk reports.
   f. Market conditions.
   g. Investment-committee guidance.

2. **Allocate capital**

   Allocation means deciding how much money to place in each investment.

   A strong idea does not automatically deserve the largest position. Position size depends on confidence, risk, liquidity, diversification, correlation with existing holdings, and mandate limits.

3. **Balance expected return and risk**

   Every investment has potential reward and potential loss. Portfolio managers try to make sure the expected reward is appropriate for the risk being taken.

   This requires both calculation and judgment. A system can calculate exposures and risk measures, but it cannot remove uncertainty from market decisions.

4. **Enforce investment mandates**

   A portfolio manager must stay within the portfolio’s agreed rules.

   A mandate may limit:

   a. Exposure to one issuer.
   b. Exposure to one sector.
   c. Exposure to one country.
   d. Exposure to a currency.
   e. Exposure to lower-rated debt.
   f. Investment in illiquid assets.
   g. Use of derivatives.
   h. Deviation from a benchmark.

   A system should identify potential breaches before a recommendation is acted upon.

5. **Monitor exposures**

   Exposure describes how much of a portfolio is affected by a company, sector, country, currency, interest-rate movement, or other factor.

   Monitoring exposure helps prevent unintended concentration. A portfolio may hold many securities but still be overly exposed to one economic outcome.

6. **Override or approve research recommendations**

   A research recommendation is input into a decision, not an automatic instruction.

   A portfolio manager may:

   a. Approve the recommendation.
   b. Reject it.
   c. Delay action.
   d. Use a smaller position size.
   e. Reduce an existing position instead of adding.
   f. Decide the recommendation does not fit the current portfolio.

   Where appropriate, the reason for an override should be preserved so the investment process remains explainable.

### 2.5.3 Core Investment Concepts

The following concepts appear regularly in investment research and portfolio intelligence.

1. **Fundamental analysis**

   Fundamental analysis studies the underlying business and financial condition of a company or asset.

   It may include revenue, profit, cash flow, debt, market position, management, industry outlook, valuation, and risk.

2. **Valuation**

   Valuation is the process of estimating what a company, security, or asset may be worth.

   Valuation can use several approaches, such as comparing companies using ratios, estimating future cash flows, or reviewing transactions involving similar businesses.

   Valuation depends on assumptions. Good systems make those assumptions visible rather than hiding them inside an unexplained recommendation.

3. **Factor screening**

   Factor screening means selecting or ranking securities using defined characteristics called factors.

   A team may screen for companies with:

   a. Strong profitability.
   b. Low debt.
   c. Attractive valuation.
   d. Stable earnings.
   e. Improving cash flow.
   f. Positive price momentum.
   g. High quality of earnings.

   Screening helps narrow a large universe. It does not replace research because a company can score well on a screen while still having material legal, governance, liquidity, or business risks.

4. **Portfolio construction**

   Portfolio construction is the process of turning individual investment ideas into a complete portfolio.

   It includes:

   a. Deciding position sizes.
   b. Balancing holdings.
   c. Managing diversification.
   d. Considering liquidity.
   e. Staying within mandate limits.
   f. Understanding how holdings interact with one another.

5. **Diversification**

   Diversification means spreading investments so that a portfolio is not overly dependent on one company, industry, country, asset type, or economic outcome.

   Diversification cannot remove all risk, but it can reduce the impact of a single investment performing badly.

6. **Sector exposure**

   Sector exposure measures how much of a portfolio is connected to an industry, such as banking, technology, healthcare, energy, or consumer goods.

   A manager may have a strong view on a sector but still limit exposure to avoid excessive concentration.

7. **Liquidity**

   Liquidity in investing means how easily an asset can be bought or sold without causing a large change in its price.

   Liquidity matters when a portfolio may need to meet client withdrawals, margin calls, or risk-reduction needs quickly.

8. **Benchmarking**

   A benchmark is a reference point used to compare portfolio performance and risk. An equity portfolio, for example, may be compared with a market index.

   Benchmarking helps a manager understand whether performance came from broad market movements, sector choices, security selection, or risk-taking different from the stated mandate.

9. **Drawdown**

   Drawdown is the decline from a portfolio’s earlier high value to a later lower value.

   For example, if a portfolio rises to 100 and later falls to 80, it has experienced a 20% drawdown from its earlier high point.

   Drawdown helps explain the severity of losses during a difficult period.

10. **Risk limits**

    Risk limits are agreed boundaries that restrict how much risk a portfolio may take.

    Limits may apply to:

    a. A single issuer.
    b. A sector.
    c. A country.
    d. A currency.
    e. An asset type.
    f. A credit rating category.
    g. An illiquid asset.
    h. A market-risk measure.

    A system can check these limits consistently. A manager may seek an approved exception, but the exception should be visible and documented.

### 2.5.4 Business Consequences of Errors

Investment research and portfolio-intelligence errors can affect clients, performance, reputation, and the organization’s ability to demonstrate disciplined decision-making.

1. **Poor investment performance**

   Incorrect data, weak analysis, poor risk controls, or delayed information can lead to losses or underperformance against a portfolio’s objective or benchmark.

2. **Mandate breaches**

   A mandate breach occurs when a portfolio exceeds an agreed limit or takes an action it was not permitted to take. This can create client, legal, regulatory, and reputational problems.

3. **Unmanaged concentration risk**

   A portfolio may become too concentrated in a company, sector, country, or factor without the team fully recognizing the impact.

4. **Delayed response to market events**

   If analysts cannot quickly find and interpret relevant information after an earnings release, market shock, or regulatory announcement, the investment team may react too slowly.

5. **Weak documentation of investment rationale**

   Investment decisions should be supported by a clear rationale, especially in institutional settings. Weak documentation makes it harder to review decisions, explain performance, learn from mistakes, and show that the team acted within its process.

---

## 2.6 Shared Business Patterns Across All Four Use Cases

The four use cases are different, but they share important business patterns. These patterns explain why the later technical chapters use similar design ideas, such as data lineage, validation, deterministic calculations, review queues, state machines, and audit records.

### 2.6.1 The Business Starts With Documents and Data

Each use case starts with information that needs to be found, read, checked, and connected.

1. **Financial document analysis** starts with annual reports, quarterly reports, financial statements, earnings presentations, and regulatory filings.

2. **KYC and enhanced due diligence** start with identity documents, ownership records, source-of-wealth evidence, screening sources, and client-provided information.

3. **Corporate credit assessment** starts with borrower financial statements, business information, loan details, industry information, and existing relationship data.

4. **Investment research** starts with company filings, market data, research materials, portfolio holdings, mandate rules, and analyst inputs.

Across all four use cases, information is often scattered across documents, spreadsheets, internal systems, and external sources. The first contribution of an agentic system is often to make information easier to find and organize.

### 2.6.2 The Process Includes Both Mechanical Work and Expert Judgment

Every workflow includes tasks that can be made more efficient and tasks that need professional judgment.

1. **Mechanical work** commonly includes:

   a. Reading documents for known fields.
   b. Copying values into a structured format.
   c. Checking whether required documents are present.
   d. Calculating ratios.
   e. Applying defined rules.
   f. Comparing values with thresholds.
   g. Building a standard case package.

2. **Expert judgment** commonly includes:

   a. Deciding whether a risk is material.
   b. Interpreting unusual business circumstances.
   c. Assessing whether a source-of-wealth explanation is credible.
   d. Understanding the significance of a declining financial trend.
   e. Deciding whether a portfolio recommendation fits the current market context.
   f. Approving, rejecting, or overriding a recommendation.

A useful system automates mechanical work where possible and gives people better information for judgment.

### 2.6.3 The Decision Has a Risk Owner

An important outcome must have an accountable owner.

1. **Financial document analysis**: An analyst or supervisory reviewer owns the publication of a research note.

2. **KYC and EDD**: A compliance officer or MLRO owns a high-risk onboarding decision.

3. **Corporate credit assessment**: A credit analyst, credit risk manager, and credit committee own lending decisions according to their authority.

4. **Investment research and portfolios**: A portfolio manager owns an investment decision, subject to the portfolio’s mandate and governance process.

An AI agent can help with preparation, evidence gathering, calculations, and explanation. It does not replace the person or committee with authority and responsibility.

### 2.6.4 The Process Needs Evidence and Traceability

Important business outputs need to be explainable.

1. A reviewer should be able to ask:

   a. What source supports this number?
   b. Which documents were used in this KYC assessment?
   c. What ownership path led to this UBO?
   d. Which risk factors produced this score?
   e. Which financial values produced this rating?
   f. Which portfolio rule caused this recommendation to be blocked?
   g. Who approved the result?
   h. Which version of the rule, model, prompt, or data source was used?

2. Traceability is important for:

   a. Regulatory review.
   b. Internal audit.
   c. Quality assurance.
   d. Analyst and reviewer trust.
   e. Investigation of errors.
   f. Reproduction of past results.
   g. Improvement of the system over time.

### 2.6.5 The Workflow Contains Exceptions

Real banking work does not follow a perfect path every time.

1. **Data and document exceptions** can include:

   a. Missing documents.
   b. Poor-quality scans.
   c. Unreadable tables.
   d. Inconsistent values.
   e. Restated financial figures.
   f. Missing required fields.

2. **Entity and identity exceptions** can include:

   a. Complex ownership structures.
   b. Unclear beneficial ownership.
   c. Same-name individuals.
   d. Conflicting identity information.
   e. Incomplete screening evidence.

3. **Decision exceptions** can include:

   a. A borrower with a weak ratio but a credible temporary explanation.
   b. A high-risk customer requiring senior approval.
   c. A portfolio recommendation that violates a mandate limit.
   d. A model output that conflicts with reliable human evidence.

The system needs clear paths for incomplete data, low confidence, conflicting evidence, escalation, manual review, and override.

### 2.6.6 The Business Wants Speed Without Losing Control

All four use cases exist because the business wants to move faster.

1. **Financial document analysis**: Analysts want to process reports faster and update research quickly.

2. **KYC and EDD**: Relationship managers and clients want legitimate onboarding to complete faster.

3. **Corporate credit assessment**: Credit teams want to prepare and review loan proposals faster.

4. **Investment research**: Investment teams want to identify and react to important information faster.

However, speed alone is not success. A fast process that produces untraceable numbers, misses sanctions concerns, misstates credit risk, or breaches portfolio limits is worse than a slower process.

The desired outcome is controlled speed: faster collection, extraction, calculation, and preparation combined with visible evidence, repeatable rules, human review, and clear accountability.

---

## 2.7 What Readers Should Carry Into Chapter 3

Chapter 3 explains why the banking processes described in this chapter need carefully designed technical systems rather than a simple chatbot or a single LLM prompt.

### 2.7.1 Automation Has Boundaries

Not every process should be fully automated. Some work requires accountable human judgment, especially where a decision affects a client, a borrower, a portfolio, or the institution’s legal and regulatory position.

### 2.7.2 Important Data Requires Validation

Not every data point should be trusted immediately. Important values need source evidence, validation, and context before they are used in calculations, scores, recommendations, or reports.

### 2.7.3 A Recommendation Is Not a Final Decision

A system may gather evidence, calculate a score, identify risk factors, or propose an action. An authorized person or committee must make consequential decisions according to the organization’s rules.

### 2.7.4 Business Value Comes From Reducing Mechanical Work

The main value of agentic AI is often not replacing professionals. It is reducing manual collection, searching, transcription, and initial preparation so that people can spend more time on analysis, judgment, review, and client work.

### 2.7.5 Technical Design Depends on the Cost of Being Wrong

The right technical design depends on the consequence of an error.

1. A missing value in a draft research note may require analyst correction.
2. A false match in a KYC case may delay or unfairly affect a customer.
3. A wrong credit rating may lead to poor pricing, losses, or policy breaches.
4. A portfolio-limit breach may create client, legal, or regulatory problems.

Chapter 3 connects these business realities to system design. It explains why banking workflows need controlled agentic AI systems with clear boundaries between language models, deterministic calculations, data stores, validation services, and human approval.
