"""
Seed complete service lines and programs for all 4 boards.
Safe to re-run — skips existing entries by name match.
"""
import sqlite3, uuid
from datetime import datetime

DB = "qci_pms.db"

BOARDS = {
    "NABL":  "ce7aa67e-722b-4f04-9dc5-9a32130b46b3",
    "NABH":  "6b7eba7c-6bc4-4213-a8e8-5ef9017c9bae",
    "NABCB": "6bc42ea1-cc75-4ccf-bdc3-c2096d113c2d",
    "NABET": "ab28aa14-85e6-4da2-9cc1-81a48eefac0f",
}

# Structure: board_code → [(sl_code, sl_name, sl_desc, sort, [programs])]
# programs: (p_code, p_name, standard_version, description, sort)
DATA = {
    "NABL": [
        ("SL_TESTING", "Testing & Calibration Laboratories",
         "Testing and calibration labs accredited under ISO/IEC 17025", 1, [
            ("P_17025T",  "Testing Laboratories",             "ISO/IEC 17025:2017", "Chemical, biological, physical, mechanical testing labs", 1),
            ("P_17025C",  "Calibration Laboratories",         "ISO/IEC 17025:2017", "Precision calibration services", 2),
            ("P_SOIL",    "Soil Testing Laboratories",        "ISO/IEC 17025:2017", "Agricultural and geotechnical soil labs", 3),
            ("P_TEMP",    "Temporary Site Laboratories",      "ISO/IEC 17025:2017", "Labs for aggregates and concrete at construction sites", 4),
            ("P_GLAP",    "Govt. Drinking Water Labs (G-LAP)","ISO/IEC 17025:2017", "Government drinking water testing laboratory accreditation", 5),
            ("P_PROD",    "Product Based Accreditation",      "ISO/IEC 17025:2017", "Product-specific accreditation scheme", 6),
        ]),
        ("SL_MEDICAL", "Medical Laboratories",
         "Medical and clinical testing labs", 2, [
            ("P_15189",   "Medical Testing Laboratories",     "ISO 15189:2022",     "Clinical and diagnostic laboratories", 1),
            ("P_MELT",    "Medical Entry Level Testing Labs", "M(EL)T",             "Entry-level recognition for medical testing labs", 2),
        ]),
        ("SL_PTP", "Proficiency Testing Providers",
         "External quality assurance through proficiency testing", 3, [
            ("P_17043",   "Proficiency Testing Providers",    "ISO/IEC 17043:2023", "Organisations running PT schemes", 1),
        ]),
        ("SL_RMP", "Reference Material Producers",
         "Producers of certified reference materials", 4, [
            ("P_17034",   "Reference Material Producers",     "ISO 17034:2016",     "Certified reference material production", 1),
        ]),
        ("SL_BIOBANK", "Biobanks",
         "Biological material repositories", 5, [
            ("P_20387",   "Biobanks",                         "ISO 20387:2018",     "Accreditation for biobanking organisations", 1),
        ]),
    ],

    "NABH": [
        ("SL_HOSP_ACC", "Hospital Accreditation",
         "Full and entry-level accreditation for hospitals and healthcare facilities", 1, [
            ("P_HCO_FULL",  "Hospital Accreditation (Full)",              None, "Comprehensive accreditation for hospitals (HCO)", 1),
            ("P_HCO_ENTRY", "Hospital Accreditation (Entry Level)",       None, "Entry-level certification for hospitals", 2),
            ("P_SHCO_FULL", "SHCO Accreditation",                         None, "Small Healthcare Organisation full accreditation", 3),
            ("P_EYE",       "Eye Care Organisations",                     None, "Accreditation for eye hospitals and clinics", 4),
            ("P_AYUSH_H",   "Ayush Hospitals",                            None, "Accreditation for Ayush (AYUSH) hospitals", 5),
            ("P_PANCHAKARMA","Panchakarma Clinics",                       None, "Accreditation for Panchakarma treatment centres", 6),
            ("P_DENTAL",    "Dental Healthcare Providers",                None, "Accreditation for dental hospitals and clinics", 7),
            ("P_CARE_HOME", "Care Homes",                                 None, "Accreditation for elder and palliative care homes", 8),
            ("P_CLINICAL_TRIAL","Clinical Trials (Ethics Committee)",     None, "Accreditation for ethics committees running clinical trials", 9),
        ]),
        ("SL_BLOOD", "Blood Banks & Transfusion Services",
         "Accreditation for blood banks and transfusion medicine", 2, [
            ("P_BLOOD",     "Blood Bank / Blood Centre Accreditation",    None, "Stand-alone blood banks and hospital blood centres", 1),
        ]),
        ("SL_IMAGING", "Medical Imaging & Laboratories",
         "Imaging services and clinical laboratories", 3, [
            ("P_IMAGING",   "Medical Imaging Services",                   None, "Radiology, imaging, and diagnostic services", 1),
            ("P_MED_LAB",   "Medical Laboratory Certification",           None, "Certification for standalone medical laboratories", 2),
            ("P_CLINIC_ACC","Allopathic Clinics",                         None, "Accreditation for allopathic outpatient clinics", 3),
        ]),
        ("SL_CERT", "Certification Programmes",
         "Entry-level and specialty certification for healthcare providers", 4, [
            ("P_ENTRY_SHCO","Entry Level SHCO Certification",             None, "Entry-level certification for small healthcare organisations", 1),
            ("P_ENTRY_AYUSH_C","Entry Level Ayush Centres Certification", None, "Entry-level certification for Ayush centres", 2),
            ("P_ENTRY_AYUSH_H","Entry Level Ayush Hospitals Certification",None,"Entry-level certification for Ayush hospitals", 3),
            ("P_NURSING",   "Nursing Excellence Certification",           None, "Certification for nursing excellence in hospitals", 4),
            ("P_EMERG",     "Emergency Department Certification",         None, "Certification for hospital emergency departments", 5),
            ("P_ENTRY_DENTAL","Entry Level Dental Clinics Certification", None, "Entry-level certification for dental clinics", 6),
            ("P_STROKE_P",  "Primary Stroke Centres Certification",       None, "Certification for primary stroke care centres", 7),
            ("P_STROKE_A",  "Advanced Stroke Centres Certification",      None, "Certification for advanced stroke care centres", 8),
            ("P_CLIMATE",   "Climate Change Resilience in Healthcare",    None, "Certification for climate-resilient healthcare organisations", 9),
        ]),
        ("SL_DIGITAL", "Digital Health Standards",
         "Standards for digital health and health IT systems", 5, [
            ("P_HIS_EMR",   "HIS/EMR Systems",                            None, "Digital health standards for Hospital Information & EMR systems", 1),
            ("P_CMS",       "Clinic Management System (CMS)",             None, "Digital health standards for clinic management software", 2),
        ]),
        ("SL_EMPANEL", "Empanelment Programmes",
         "Empanelment with government and insurance schemes", 6, [
            ("P_CGHS",      "CGHS Empanelment",                           None, "Central Government Health Scheme empanelment", 1),
            ("P_ECHS",       "ECHS Empanelment",                          None, "Ex-Servicemen Contributory Health Scheme empanelment", 2),
            ("P_MVTF",      "Medical Value Travel Facilitator (MVTF)",    None, "Empanelment for medical tourism facilitators", 3),
            ("P_PMJAY",     "AB-PMJAY Quality Certification",             None, "Ayushman Bharat quality certification for hospitals", 4),
        ]),
    ],

    "NABCB": [
        ("SL_CB", "Certification Bodies",
         "Accreditation for management system, product, and personnel certification bodies", 1, [
            ("P_17021",  "Management System Certification Bodies", "ISO/IEC 17021-1:2015", "Accreditation for bodies certifying management systems (ISO 9001, 14001, 45001 etc.)", 1),
            ("P_17065",  "Product & Process Certification Bodies", "ISO/IEC 17065:2012",   "Accreditation for bodies certifying products, processes and services", 2),
            ("P_17024",  "Personnel Certification Bodies",         "ISO/IEC 17024:2012",   "Accreditation for bodies certifying individual competence", 3),
        ]),
        ("SL_IB", "Inspection Bodies",
         "Accreditation for third-party inspection organisations", 2, [
            ("P_17020",  "Inspection Bodies",                      "ISO/IEC 17020:2012",   "Accreditation for Type A, B, C inspection bodies", 1),
        ]),
        ("SL_VVB", "Validation & Verification Bodies",
         "Accreditation for GHG and sustainability validation/verification", 3, [
            ("P_14065",  "Validation & Verification Bodies",       "ISO 14065:2020",        "GHG emissions validation and verification bodies", 1),
        ]),
    ],

    "NABET": [
        ("SL_SCHOOL", "School Accreditation",
         "Quality accreditation for formal educational institutions", 1, [
            ("P_SCHOOL",    "School Quality Certification",        None, "Accreditation for schools through FEED (Formal Education Excellence Division)", 1),
        ]),
        ("SL_ENV", "Environment Consultants",
         "Accreditation for environment impact and clearance consultants", 2, [
            ("P_EIA_A",  "EIA Consultant Organisations – Category A", None, "Environmental Impact Assessment consultants for Category A projects", 1),
            ("P_EIA_B",  "EIA Consultant Organisations – Category B", None, "Environmental Impact Assessment consultants for Category B projects", 2),
            ("P_GWCO",   "Ground Water Consultant Organisations",     None, "Accreditation for groundwater assessment and advisory consultants", 3),
            ("P_FCCO",   "Forest Clearance Consultant Organisations", None, "Accreditation for forest clearance advisory consultants", 4),
        ]),
        ("SL_MINING", "Mining & Exploration Agencies",
         "Accreditation for mineral prospecting, exploration and mining plan agencies", 3, [
            ("P_APA",   "Prospecting/Exploration Agencies (APA)",  None, "Mineral prospecting and exploration agency accreditation", 1),
            ("P_MPPA",  "Mining Plan Preparing Agencies (MPPA)",   None, "Accreditation for agencies preparing statutory mining plans", 2),
            ("P_AEA",   "Exploration Agencies for Minerals (AEA)", None, "Mineral sector exploration agency accreditation", 3),
        ]),
        ("SL_SKILL", "Skill & Training",
         "Accreditation for skill certification and vocational training bodies", 4, [
            ("P_RACB",  "Skill Certification Bodies (RACB)",       None, "Recognition of accredited certification bodies for skill assessment", 1),
            ("P_LMCS",  "Lean Manufacturing Competitiveness (LMCS)",None, "Lean manufacturing competitiveness scheme for MSMEs", 2),
            ("P_PHARM", "Pharmacy College Ranking",                 None, "Ranking and rating of UG and D. Pharmacy colleges", 3),
            ("P_TCB",   "Training & Capacity Building (TCB)",       None, "Accreditation for vocational training organisations", 4),
        ]),
    ],
}


def slug(name: str) -> str:
    return name.upper().replace(" ", "_").replace("/", "_").replace("-", "_")[:30]


def main():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()
    NOW = datetime.utcnow().isoformat()

    for board_code, service_lines in DATA.items():
        board_id = BOARDS[board_code]
        print(f"\n=== {board_code} ===")

        for (sl_code, sl_name, sl_desc, sl_sort, programs) in service_lines:
            # Upsert service line by name
            c.execute("SELECT id FROM service_lines WHERE board_id=? AND name=?", (board_id, sl_name))
            row = c.fetchone()
            if row:
                sl_id = row[0]
                print(f"  [EXIST] {sl_name}")
            else:
                sl_id = str(uuid.uuid4())
                c.execute("""
                    INSERT INTO service_lines (id, board_id, code, name, description, sort_order, is_active)
                    VALUES (?,?,?,?,?,?,1)
                """, (sl_id, board_id, sl_code, sl_name, sl_desc, sl_sort))
                print(f"  [ADD]   {sl_name}")

            for (p_code, p_name, p_std, p_desc, p_sort) in programs:
                c.execute("SELECT id FROM programs WHERE service_line_id=? AND name=?", (sl_id, p_name))
                if c.fetchone():
                    print(f"           · SKIP {p_name}")
                else:
                    c.execute("""
                        INSERT INTO programs (id, service_line_id, board_id, code, name,
                                             description, standard_version, sort_order, is_active)
                        VALUES (?,?,?,?,?,?,?,?,1)
                    """, (str(uuid.uuid4()), sl_id, board_id, p_code, p_name, p_desc, p_std, p_sort))
                    print(f"           · ADD  {p_name}")

    conn.commit()
    conn.close()
    print("\n✅ Programs seed complete.")


if __name__ == "__main__":
    main()
