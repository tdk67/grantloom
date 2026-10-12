"""Curated domain specifications and planted ground-truth facts for synthetic grant data."""

from typing import List, Tuple
from app.synthetic.excel_builder import SheetSpec
from app.synthetic.models import DocumentDefinition, PlantedFact, SectionContent


def get_planted_facts() -> List[PlantedFact]:
    """Returns golden evaluation benchmark facts planted across the synthetic dataset."""
    return [
        PlantedFact(
            fact_id="FACT_001_COST_SHARE",
            question="What is the mandatory non-federal cost share percentage for the DOE Solar 2026 FOA?",
            expected_answer="The mandatory non-federal cost share is exactly 20.0% of total allowable project costs.",
            document_name="DOE_Solar_2026_Sample.pdf",
            location_hint="Section 3: Eligibility & Cost Sharing Requirements",
            keywords=["cost share", "20%", "non-federal", "mandatory"],
            numeric_value=20.0,
        ),
        PlantedFact(
            fact_id="FACT_002_EQUIPMENT_THRESHOLD",
            question="What capital equipment dollar threshold requires prior written Contracting Officer approval?",
            expected_answer="Any individual capital equipment item exceeding $50,000 requires prior written Contracting Officer approval.",
            document_name="DOE_Solar_2026_Sample.pdf",
            location_hint="Section 4: Allowable Costs & Capital Equipment Thresholds",
            keywords=["equipment", "$50,000", "prior written approval", "threshold"],
            numeric_value=50000.0,
        ),
        PlantedFact(
            fact_id="FACT_003_PI_ELIGIBILITY",
            question="What are the minimum qualification requirements for the Principal Investigator under the DOE Solar FOA?",
            expected_answer="The Principal Investigator must possess a minimum of 5 years post-terminal degree leadership in renewable grid engineering or power systems.",
            document_name="DOE_Solar_2026_Sample.pdf",
            location_hint="Section 3: Principal Investigator Eligibility",
            keywords=["5 years", "post-terminal", "leadership", "Principal Investigator"],
            numeric_value=5.0,
        ),
        PlantedFact(
            fact_id="FACT_004_SUBMISSION_DEADLINE",
            question="What is the formal submission deadline for proposals under the DOE Solar 2026 FOA?",
            expected_answer="Proposals must be submitted by November 15, 2026, at 5:00 PM Eastern Time via Grants.gov.",
            document_name="DOE_Solar_2026_Sample.pdf",
            location_hint="Section 5: Submission Dates and Times",
            keywords=["November 15, 2026", "5:00 PM Eastern Time", "deadline"],
        ),
        PlantedFact(
            fact_id="FACT_005_TOTAL_FUNDING",
            question="What is the total anticipated funding pool and award count for the DOE Solar FOA?",
            expected_answer="Total anticipated funding is $45,000,000 across approximately 12 to 18 awards.",
            document_name="DOE_Solar_2026_Sample.pdf",
            location_hint="Section 2: Award Information",
            keywords=["$45,000,000", "12 to 18 awards", "total funding"],
            numeric_value=45000000.0,
        ),
        PlantedFact(
            fact_id="FACT_006_PI_NAME_FACILITY",
            question="Who is the Principal Investigator and what primary testing facility is cited in the past lab proposal?",
            expected_answer="The Principal Investigator is Dr. Elena Vance, utilizing the Grid Edge Testing Laboratory at the Baltimore Innovation Center.",
            document_name="Past_Proposal_2024_Sample.pdf",
            location_hint="Section 3: Facilities, Equipment & Biosketch",
            keywords=["Dr. Elena Vance", "Baltimore Innovation Center", "Grid Edge Testing Laboratory"],
        ),
        PlantedFact(
            fact_id="FACT_007_PRIOR_UPTIME",
            question="What pilot performance uptime metric did the lab achieve in prior East Baltimore deployments?",
            expected_answer="The lab achieved 99.4% uptime across 4 pilot microgrids deployed in East Baltimore.",
            document_name="Past_Proposal_2024_Sample.pdf",
            location_hint="Section 4: Prior Track Record & Performance",
            keywords=["99.4%", "uptime", "4 pilot microgrids", "East Baltimore"],
            numeric_value=99.4,
        ),
        PlantedFact(
            fact_id="FACT_008_TRAVEL_TOTAL",
            question="What is the total travel budget requested in the lab budget spreadsheet?",
            expected_answer="The total travel budget is exactly $12,450.",
            document_name="Lab_Budget_2025_Sample.xlsx",
            location_hint="Sheet 'Travel', Cell H5",
            expected_sheet="Travel",
            expected_cell="H5",
            keywords=["travel", "$12,450", "total"],
            numeric_value=12450.0,
        ),
        PlantedFact(
            fact_id="FACT_009_EQUIPMENT_TOTAL",
            question="What is the total equipment cost requested in the lab budget spreadsheet?",
            expected_answer="The total equipment cost is $68,000 across three line items.",
            document_name="Lab_Budget_2025_Sample.xlsx",
            location_hint="Sheet 'Equipment', Cell E5",
            expected_sheet="Equipment",
            expected_cell="E5",
            keywords=["equipment", "$68,000", "total"],
            numeric_value=68000.0,
        ),
        PlantedFact(
            fact_id="FACT_010_EQUIPMENT_APPROVAL_REQUIRED",
            question="Does any individual equipment item in the lab budget spreadsheet exceed the $50,000 approval threshold?",
            expected_answer="No single equipment item exceeds $50,000 (the highest is the Bi-Directional DC Power Supply at $32,000). Therefore, no individual pre-approval is required under the threshold rule.",
            document_name="Lab_Budget_2025_Sample.xlsx",
            location_hint="Sheet 'Equipment', Rows 2-4",
            expected_sheet="Equipment",
            keywords=["exceed", "threshold", "$50,000", "No", "$32,000"],
            numeric_value=0.0,
        ),
    ]


def build_doe_solar_nofo_spec() -> DocumentDefinition:
    """Builds a realistic ~25-page DOE Funding Opportunity Announcement spec."""
    sections: List[SectionContent] = []

    # Section 1: Executive Summary & Table of Contents
    sections.append(
        SectionContent(
            title="1. Funding Opportunity Announcement Overview",
            level=1,
            paragraphs=[
                "The United States Department of Energy (DOE), Office of Energy Efficiency and Renewable Energy "
                "(EERE), Solar Energy Technologies Office (SETO) announces this competitive funding opportunity "
                "for research, development, and demonstration of advanced photovoltaic grid integration technologies.",
                "Grid-forming solar inverters, distributed energy resource management systems (DERMS), and community-level "
                "resilience architectures are crucial for modernizing the national electric grid while ensuring reliability, "
                "affordability, and cybersecurity across diverse operating conditions.",
            ],
            bullet_points=[
                "Funding Opportunity Number: DE-FOA-0003291",
                "CFDA Number: 81.087 — Renewable Energy Research and Development",
                "Issuing Agency: U.S. Department of Energy, Golden Field Office",
                "Eligible Applicants: Higher education institutions, non-profit research organizations, and commercial entities",
            ],
            callout_box="Statutory Notice: All applications must be submitted electronically through Grants.gov. Late submissions will not be reviewed.",
            page_break_after=True,
        )
    )

    # Section 2: Award Information & Scope
    sections.append(
        SectionContent(
            title="2. Award Information and Program Scope",
            level=1,
            paragraphs=[
                "EERE SETO anticipates providing approximately $45,000,000 in total federal funding across approximately "
                "12 to 18 cooperative agreements under this announcement, subject to congressional appropriations.",
                "Individual project awards are expected to range from an award floor of $1,500,000 to an award ceiling "
                "of $5,000,000 over a performance period of 24 to 36 months.",
                "Awardees will be required to participate in quarterly programmatic reviews, adhere to milestone-driven "
                "go/no-go decision points, and report financial progress in accordance with federal regulations.",
            ],
            table_data=[
                ["Topic Area", "Anticipated Awards", "Award Floor", "Award Ceiling", "Total Sub-budget"],
                ["Topic 1: Grid-Forming Inverter Controls", "4 - 6", "$1,500,000", "$3,500,000", "$15,000,000"],
                ["Topic 2: Urban Microgrid Resilience & Storage", "4 - 6", "$2,000,000", "$4,000,000", "$18,000,000"],
                ["Topic 3: Cybersecurity for Distributed Energy", "4 - 6", "$1,500,000", "$3,000,000", "$12,000,000"],
            ],
            page_break_after=True,
        )
    )

    # Section 3: Eligibility & Cost Sharing (Planted Fact 1 & 3)
    sections.append(
        SectionContent(
            title="3. Eligibility Information and Mandatory Cost Sharing",
            level=1,
            paragraphs=[
                "Eligibility is restricted to domestic entities incorporated in the United States. Foreign entities "
                "may only participate as subrecipients subject to explicit Contracting Officer waiver approval.",
                "Principal Investigator (PI) Qualifications: The Lead Principal Investigator designated on the application "
                "must demonstrate a documented record of at least 5 years post-terminal degree leadership in renewable "
                "grid engineering, electrical power systems, or related energy disciplines.",
                "Cost Share Requirement: In accordance with Section 988 of the Energy Policy Act of 2005, applicants "
                "must provide a minimum non-federal cost share of exactly 20.0% of total allowable project costs for "
                "all research and development activities.",
            ],
            bullet_points=[
                "Cost share must come from non-federal sources (cash, equipment donations, third-party in-kind contributions).",
                "Federal funds from other grants cannot be counted toward the 20.0% cost share requirement.",
                "Proposals failing to meet the minimum 20.0% non-federal cost match will be deemed non-responsive and disqualified prior to merit review.",
            ],
            callout_box="MANDATORY REQUIREMENT: Exactly 20.0% non-federal cost share is required across all budget periods. Cost share commitments must be verified via signed third-party letters of commitment.",
            page_break_after=True,
        )
    )

    # Section 4: Budget Rules & Equipment Threshold (Planted Fact 2)
    sections.append(
        SectionContent(
            title="4. Allowable Costs and Capital Equipment Thresholds",
            level=1,
            paragraphs=[
                "All proposed costs must be allowable, allocable, and reasonable under 2 CFR 200 Uniform Guidance. "
                "Applicants must provide a detailed Budget Justification broken down by federal share and cost share.",
                "Capital Equipment Policy: Equipment is defined as tangible personal property having a useful life "
                "of more than one year and a per-unit acquisition cost of $5,000 or greater.",
                "Prior Approval Requirement: Any individual capital equipment purchase with an acquisition cost "
                "exceeding $50,000 requires prior written approval from the DOE Contracting Officer before procurement. "
                "Applicants requesting equipment items above $50,000 must include at least three vendor price quotations "
                "and an engineering justification in Appendix C.",
            ],
            bullet_points=[
                "General-purpose office computers and furniture are unallowable as direct charges.",
                "Domestic travel must comply with the Fly America Act (49 U.S.C. 40118).",
                "Indirect costs (F&A) are reimbursable based on an approved Negotiated Indirect Cost Rate Agreement (NICRA) or standard 15% de minimis.",
            ],
            callout_box="EQUIPMENT THRESHOLD: Any single equipment item exceeding $50,000 requires prior written Contracting Officer approval prior to purchase.",
            page_break_after=True,
        )
    )

    # Section 5: Application Submission Dates (Planted Fact 4)
    sections.append(
        SectionContent(
            title="5. Application Submission Dates, Times, and Formatting",
            level=1,
            paragraphs=[
                "Full applications must be submitted through Grants.gov no later than November 15, 2026, at 5:00 PM Eastern Time.",
                "DOE strongly recommends submitting the complete application package at least 48 hours prior to the deadline "
                "to allow for validation through Grants.gov and EERE Exchange systems.",
                "Applications received after the November 15, 2026 5:00 PM Eastern Time deadline will not be reviewed or considered.",
            ],
            bullet_points=[
                "Page limit: Project Narrative must not exceed 25 pages, excluding biosketches and budget workbooks.",
                "Font: Standard 12-point font (Times New Roman, Arial, or Calibri) with 1-inch margins on all sides.",
                "File format: PDF format for narrative documents; Excel (.xlsx) format for budget justification workbooks.",
            ],
            page_break_after=True,
        )
    )

    # Section 6: Review Criteria & Merit Scoring
    sections.append(
        SectionContent(
            title="6. Merit Review Criteria and Evaluation Rubric",
            level=1,
            paragraphs=[
                "Applications will be evaluated by an independent peer review panel in accordance with the following criteria:",
            ],
            table_data=[
                ["Criterion", "Weight", "Description"],
                ["Criterion 1: Technical Merit & Innovation", "35%", "Novelty of approach, scientific validity, and potential impact on grid resilience"],
                ["Criterion 2: Work Plan & Feasibility", "30%", "Milestone realism, risk mitigation, go/no-go decision gates, and timeline clarity"],
                ["Criterion 3: Team Qualifications & Resources", "20%", "Investigator expertise, laboratory facilities, and institutional commitment"],
                ["Criterion 4: Community Benefits & Equity", "15%", "Diversity, equity, inclusion, energy justice, and quality job creation plan"],
            ],
            page_break_after=True,
        )
    )

    # Additional sections to achieve ~25 pages of realistic content
    for page_idx in range(7, 26):
        sections.append(
            SectionContent(
                title=f"{page_idx}. Program Guidelines & Statutory Appendix {chr(65 + page_idx - 7)}",
                level=2,
                paragraphs=[
                    f"This section outlines detailed administrative guidelines for Part {page_idx} of DE-FOA-0003291. "
                    "Recipients must maintain compliance with all relevant federal regulations, including environmental "
                    "policy compliance (NEPA), intellectual property rights under Bayh-Dole Act, and export control regulations.",
                    "Detailed technical standards include IEEE 1547-2018 for interconnection and interoperability of distributed energy "
                    "resources, UL 1741-SB for smart inverter testing, and NIST Cybersecurity Framework version 2.0.",
                ],
                bullet_points=[
                    f"Compliance checkpoint {page_idx}.1: Environmental impact review documentation",
                    f"Compliance checkpoint {page_idx}.2: Quarterly progress reporting via SF-425 and Research Performance Progress Report (RPPR)",
                    f"Compliance checkpoint {page_idx}.3: Data Management Plan ensuring public open access to validated datasets within 180 days of publication",
                ],
                page_break_after=True,
            )
        )

    return DocumentDefinition(
        doc_id="DE-FOA-0003291",
        filename="DOE_Solar_2026_Sample.pdf",
        title="DE-FOA-0003291: Advanced Solar Photovoltaic Grid Integration & Resilience",
        subtitle="U.S. Department of Energy | Solar Energy Technologies Office (SETO)",
        author_or_agency="U.S. Department of Energy (DOE)",
        date_str="2026-09-01",
        doc_type="solicitation_pdf",
        sections=sections,
        metadata={"nofo_number": "DE-FOA-0003291", "cfda": "81.087", "target_pages": 25},
    )


def build_past_proposal_spec() -> DocumentDefinition:
    """Builds a realistic ~15-page past winning proposal from Community Health Innovation Lab."""
    sections: List[SectionContent] = []

    # Section 1: Executive Summary & Abstract
    sections.append(
        SectionContent(
            title="1. Project Abstract and Executive Summary",
            level=1,
            paragraphs=[
                "Project Title: Urban Microgrid Solar Storage Integration for Underserved Neighborhoods",
                "Applicant Organization: Community Health Innovation Lab (CHIL), Baltimore, MD",
                "Principal Investigator: Dr. Elena Vance, Lead Energy Systems Scientist",
                "Federal Award Identifier: DE-EE0009871 (Awarded $1,250,000 for FY2024-2026)",
                "This project demonstrates an autonomous microgrid control architecture combining rooftop solar photovoltaic "
                "arrays, community battery energy storage, and smart inverter islanding algorithms to provide continuous "
                "emergency power to healthcare and cooling centers in East Baltimore.",
            ],
            bullet_points=[
                "Total Federal Award: $1,250,000 (DOE EERE SETO)",
                "Non-Federal Matching Share: $312,500 (20.0% cost match provided by Maryland Clean Energy Center)",
                "Performance Period: October 1, 2024 through September 30, 2026",
            ],
            page_break_after=True,
        )
    )

    # Section 2: Specific Aims & Technical Architecture
    sections.append(
        SectionContent(
            title="2. Specific Aims and Technical Approach",
            level=1,
            paragraphs=[
                "Aim 1: Design and validate high-speed sub-cycle islanding detection algorithms compliant with IEEE 1547-2018.",
                "Aim 2: Implement dynamic state estimation and secondary voltage regulation across distributed battery storage units.",
                "Aim 3: Deploy real-world hardware-in-the-loop (HIL) testbeds to validate microgrid black-start capabilities during grid outages.",
            ],
            bullet_points=[
                "Sub-second fault isolation prevents feeder trips during distribution line transients.",
                "State-of-charge balancing algorithm extends lithium-ion battery pack lifespan by an estimated 22%.",
                "Open-source telemetry stack ensures interoperability with municipal emergency response coordinators.",
            ],
            page_break_after=True,
        )
    )

    # Section 3: Facilities, Equipment & Biosketch (Planted Fact 6)
    sections.append(
        SectionContent(
            title="3. Facilities, Equipment, and Key Personnel Biosketch",
            level=1,
            paragraphs=[
                "Primary Research Facility: Community Health Innovation Lab operates the Grid Edge Testing Laboratory "
                "located within the Baltimore Innovation Center (450 E 29th St, Baltimore, MD).",
                "Facility Capabilities: The laboratory contains 4,500 sq ft of dedicated testing space, featuring a 100kW "
                "regenerative AC grid emulator, real-time digital simulator (RTDS), high-voltage DC test bench, and programmable "
                "photovoltaic array simulators.",
                "Principal Investigator Biosketch — Dr. Elena Vance:",
                "Dr. Elena Vance serves as Lead Energy Systems Scientist at CHIL. She holds a Ph.D. in Electrical and Computer "
                "Engineering from Johns Hopkins University (2014) with over 12 years of specialized leadership in microgrid controls, "
                "power electronics, and renewable energy resilience. Dr. Vance has authored 24 peer-reviewed journal papers and holds "
                "two patents in inverter islanding protection.",
            ],
            bullet_points=[
                "Dr. Elena Vance, Ph.D. — Principal Investigator (25% effort / 3.0 person months)",
                "Marcus Holloway, M.S. — Lead Firmware Engineer (50% effort / 6.0 person months)",
                "Dr. Aisha Patel, Ph.D. — Community Equity & Policy Lead (15% effort / 1.8 person months)",
            ],
            callout_box="PERSONNEL NOTICE: Dr. Elena Vance possesses 12 years of post-doctoral leadership, exceeding the 5-year PI requirement.",
            page_break_after=True,
        )
    )

    # Section 4: Prior Track Record & Performance (Planted Fact 7)
    sections.append(
        SectionContent(
            title="4. Prior Track Record and Demonstrated Performance",
            level=1,
            paragraphs=[
                "CHIL has successfully executed three municipal resilience grants with uninterrupted operational compliance.",
                "Operational Metric: During the 2024-2025 pilot phase in East Baltimore, CHIL achieved a verified 99.4% uptime "
                "across 4 pilot microgrids deployed at community healthcare and emergency shelter facilities.",
                "During a major grid outage event in August 2025, the CHIL microgrid seamlessly transitioned to islanded operation "
                "in 38 milliseconds, maintaining full power to critical medical refrigeration for 36 consecutive hours.",
            ],
            bullet_points=[
                "Demonstrated 99.4% system availability across 4 pilot microgrid deployments in East Baltimore.",
                "Zero loss of critical refrigeration during utility blackout events.",
                "100% adherence to DOE financial reporting deadlines and clean A-133 single audit findings.",
            ],
            page_break_after=True,
        )
    )

    # Sections 5-15 to achieve ~15 pages
    for p_idx in range(5, 16):
        sections.append(
            SectionContent(
                title=f"{p_idx}. Technical Narrative & Risk Mitigation Section {p_idx}",
                level=2,
                paragraphs=[
                    f"Detailed operational protocol {p_idx}: CHIL implements strict quality assurance following ISO 9001 standards. "
                    "Hardware testing protocols mandate thermal imaging inspections, electromagnetic interference (EMI) scans, "
                    "and firmware regression suites prior to live distribution system connection.",
                    "Community engagement teams conduct monthly public workshops with East Baltimore neighborhood associations, "
                    "ensuring transparent knowledge sharing and resident input on siting priorities.",
                ],
                bullet_points=[
                    f"Safety Protocol {p_idx}.A: NFPA 855 compliance for stationary energy storage systems",
                    f"Telemetry Protocol {p_idx}.B: IEEE 2030.5 smart energy profile encryption",
                    f"Verification Protocol {p_idx}.C: Hardware-in-the-loop stress testing under simulated line drop conditions",
                ],
                page_break_after=True,
            )
        )

    return DocumentDefinition(
        doc_id="CHIL-PROP-2024-01",
        filename="Past_Proposal_2024_Sample.pdf",
        title="Urban Microgrid Solar Storage Integration for Underserved Neighborhoods",
        subtitle="Federal Proposal Narrative | Award DE-EE0009871",
        author_or_agency="Community Health Innovation Lab (CHIL)",
        date_str="2024-03-15",
        doc_type="proposal_pdf",
        sections=sections,
        metadata={"applicant": "CHIL", "pi": "Dr. Elena Vance", "target_pages": 15},
    )


def build_lab_budget_specs() -> List[SheetSpec]:
    """Builds multi-sheet budget specification for Lab_Budget_2025_Sample.xlsx."""
    sheets: List[SheetSpec] = []

    # Sheet 1: Personnel
    personnel_headers = [
        "Position / Title",
        "Name",
        "Annual Base Salary",
        "% Effort (FTE)",
        "Fringe Rate",
        "Fringe Benefits",
        "Total Requested",
    ]
    personnel_rows = [
        ["Principal Investigator", "Dr. Elena Vance", 140000, 0.25, 0.28, "=C2*D2*E2", "=C2*D2+F2"],
        ["Senior Firmware Engineer", "Marcus Holloway", 110000, 0.50, 0.28, "=C3*D3*E3", "=C3*D3+F3"],
        ["Community Equity Lead", "Dr. Aisha Patel", 95000, 0.15, 0.28, "=C4*D4*E4", "=C4*D4+F4"],
        ["Graduate Student Researcher", "TBD (JHU Fellow)", 45000, 0.50, 0.10, "=C5*D5*E5", "=C5*D5+F5"],
    ]
    personnel_total = ["Personnel Subtotal", "", "", "", "", "=SUM(F2:F5)", "=SUM(G2:G5)"]
    personnel_formats = {3: "$#,##0", 4: "0.0%", 5: "0.0%", 6: "$#,##0", 7: "$#,##0"}
    sheets.append(
        SheetSpec(
            title="Personnel",
            headers=personnel_headers,
            rows=personnel_rows,
            total_row=personnel_total,
            column_formats=personnel_formats,
            note="Project Personnel Direct Costs — 12-Month Budget Period 1",
        )
    )

    # Sheet 2: Travel (Planted Fact 8: Total = $12,450)
    travel_headers = [
        "Trip Destination & Event",
        "Purpose",
        "# Travelers",
        "# Days",
        "Airfare / Transit",
        "Lodging & Meals",
        "Registration",
        "Total Cost",
    ]
    travel_rows = [
        ["Washington, DC (DOE Annual Peer Review)", "Required Annual Project Presentation", 2, 3, 1200, 2050, 600, "=E2+F2+G2"],
        ["Denver, CO (IEEE PES General Meeting)", "Disseminate Inverter Research Papers", 2, 4, 1600, 2200, 800, "=E3+F3+G3"],
        ["Baltimore & Annapolis, MD (Field Sites)", "Bi-Weekly Community Field Inspections", 2, 12, 1800, 2200, 0, "=E4+F4+G4"],
    ]
    travel_total = ["Travel Subtotal", "", "", "", "=SUM(E2:E4)", "=SUM(F2:F4)", "=SUM(G2:G4)", "=SUM(H2:H4)"]
    travel_formats = {5: "$#,##0", 6: "$#,##0", 7: "$#,##0", 8: "$#,##0"}
    sheets.append(
        SheetSpec(
            title="Travel",
            headers=travel_headers,
            rows=travel_rows,
            total_row=travel_total,
            column_formats=travel_formats,
            note="Domestic Travel Schedule — All air travel complies with Fly America Act",
        )
    )

    # Sheet 3: Equipment (Planted Fact 9: Total = $68,000; Fact 10: None > $50k)
    equip_headers = [
        "Item Description",
        "Manufacturer / Model",
        "Quantity",
        "Unit Cost",
        "Total Cost",
        "Exceeds $50k Threshold?",
    ]
    equip_rows = [
        ["High-Precision Power Quality Analyzer", "Chroma 66205", 1, 18000, "=C2*D2", "No ($18,000)"],
        ["Bi-Directional DC Power Supply (15kW)", "Magna-Power XR Series", 1, 32000, "=C3*D3", "No ($32,000)"],
        ["High-Voltage Isolation Test Enclosure", "Custom Faraday RF-Shield", 1, 18000, "=C4*D4", "No ($18,000)"],
    ]
    equip_total = ["Equipment Subtotal", "", "=SUM(C2:C4)", "", "=SUM(E2:E4)", "All items < $50,000"]
    equip_formats = {4: "$#,##0", 5: "$#,##0"}
    sheets.append(
        SheetSpec(
            title="Equipment",
            headers=equip_headers,
            rows=equip_rows,
            total_row=equip_total,
            column_formats=equip_formats,
            note="Capital Equipment Schedule — Individual items exceeding $50k require Contracting Officer prior approval",
        )
    )

    # Sheet 4: Summary Budget & Indirect Costs
    summary_headers = ["Budget Category", "Direct Cost", "MTDC Excluded", "Base for F&A", "Notes / Justification"]
    summary_rows = [
        ["A. Personnel (Salaries & Fringe)", 158190, 0, 158190, "From Personnel Sheet (4 key staff)"],
        ["B. Travel (Domestic Conferences & Sites)", 12450, 0, 12450, "From Travel Sheet (DOE Review + IEEE PES)"],
        ["C. Equipment (Capital items > $5k)", 68000, 68000, 0, "Excluded from MTDC under 2 CFR 200"],
        ["D. Supplies & Testing Consumables", 16500, 0, 16500, "Cables, sensors, load resistors"],
        ["E. Total Direct Costs (A+B+C+D)", "=SUM(B2:B5)", "=SUM(C2:C5)", "=SUM(D2:D5)", "Sum of all direct categories"],
        ["F. Modified Total Direct Cost (MTDC)", "=D6", 0, "=D6", "Allowable F&A base"],
        ["G. Indirect Costs (F&A @ 15.0% De Minimis)", "=D7*0.15", 0, 0, "15% standard non-profit de minimis rate"],
    ]
    summary_total = ["TOTAL PROJECT BUDGET (Direct + Indirect)", "=B6+B8", "", "", "100% compliant with DOE cost principles"]
    summary_formats = {2: "$#,##0", 3: "$#,##0", 4: "$#,##0"}
    sheets.append(
        SheetSpec(
            title="Summary_Budget",
            headers=summary_headers,
            rows=summary_rows,
            total_row=summary_total,
            column_formats=summary_formats,
            note="Consolidated 12-Month Project Budget Summary",
        )
    )

    return sheets


def build_compliance_matrix_spec() -> SheetSpec:
    """Builds structured compliance checklist mapping NOFO requirements to proposal sections."""
    headers = [
        "Requirement ID",
        "NOFO Requirement Description",
        "Mandatory?",
        "Target Proposal Section",
        "Compliance Status",
        "Reviewer Evidence / Audit Trail",
    ]
    rows = [
        [
            "REQ-01",
            "Non-Federal Cost Share >= 20.0%",
            "Mandatory",
            "Proposal Section 1 & Budget Sheet",
            "Satisfied",
            "Commitment letter confirms 20.0% ($312,500) non-federal match from MD Clean Energy Center",
        ],
        [
            "REQ-02",
            "PI Leadership >= 5 years in renewable grid engineering",
            "Mandatory",
            "Proposal Section 3 (Biosketch)",
            "Satisfied",
            "Dr. Elena Vance holds Ph.D. (2014) and 12 years post-terminal leadership with 24 papers",
        ],
        [
            "REQ-03",
            "Prior approval for equipment exceeding $50,000 threshold",
            "Mandatory",
            "Budget Sheet 'Equipment'",
            "Satisfied",
            "No single equipment item exceeds $50k (max single item is $32,000 DC power supply)",
        ],
        [
            "REQ-04",
            "Domestic applicant entity incorporated in USA",
            "Mandatory",
            "Proposal Section 1 (Cover)",
            "Satisfied",
            "Community Health Innovation Lab is a 501(c)(3) incorporated in Maryland, USA",
        ],
        [
            "REQ-05",
            "Compliance with Fly America Act for all air transit",
            "Mandatory",
            "Budget Sheet 'Travel'",
            "Satisfied",
            "Budget narrative specifically cites Fly America Act for DC and Denver airfares",
        ],
        [
            "REQ-06",
            "Community Benefits and Energy Justice plan included",
            "Mandatory",
            "Proposal Section 4 & Appendix D",
            "Satisfied",
            "Dedicated Community Equity Lead (Dr. Aisha Patel) and monthly East Baltimore workshops",
        ],
    ]
    return SheetSpec(
        title="Compliance_Checklist",
        headers=headers,
        rows=rows,
        note="Audit Matrix: DE-FOA-0003291 Solicitation Requirements vs CHIL Proposal",
    )
