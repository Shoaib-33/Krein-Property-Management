"""Additive migrations; existing tenant and financial records are never reseeded."""

V2 = '''
CREATE TABLE portal_accounts (
 user_id INTEGER NOT NULL REFERENCES users(id), tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,tenant_id), UNIQUE(tenant_id)
);
CREATE TABLE contractors (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', trade TEXT NOT NULL,
 agreement_document_id INTEGER REFERENCES documents(id), active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL
);
CREATE TABLE contractor_accounts (
 user_id INTEGER NOT NULL REFERENCES users(id), contractor_id INTEGER NOT NULL UNIQUE REFERENCES contractors(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,contractor_id)
);
CREATE TABLE account_invitations (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), email TEXT NOT NULL,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('tenant','contractor')),
 target_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
 used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE shared_documents (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 document_id INTEGER NOT NULL REFERENCES documents(id), shared_by INTEGER NOT NULL REFERENCES users(id),
 shared_at TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0, UNIQUE(document_id,contract_id)
);
CREATE TABLE portal_messages (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 sender_id INTEGER NOT NULL REFERENCES users(id), subject TEXT NOT NULL, body TEXT NOT NULL,
 document_id INTEGER REFERENCES documents(id), created_at TEXT NOT NULL
);
CREATE TABLE requests (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 kind TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL, requested_date TEXT,
 status TEXT NOT NULL DEFAULT 'open', response TEXT, responded_by INTEGER REFERENCES users(id),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, responded_at TEXT
);
CREATE TABLE notifications (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), user_id INTEGER NOT NULL REFERENCES users(id),
 title TEXT NOT NULL, body TEXT NOT NULL, target TEXT NOT NULL, is_read INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE repair_updates (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), repair_id INTEGER NOT NULL REFERENCES repairs(id),
 user_id INTEGER NOT NULL REFERENCES users(id), message TEXT NOT NULL, document_id INTEGER REFERENCES documents(id),
 tenant_visible INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE TABLE work_orders (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), repair_id INTEGER NOT NULL REFERENCES repairs(id),
 contractor_id INTEGER NOT NULL REFERENCES contractors(id), scope TEXT NOT NULL, scheduled_for TEXT,
 status TEXT NOT NULL DEFAULT 'assigned', quote_cents INTEGER, quote_note TEXT, quote_status TEXT NOT NULL DEFAULT 'none',
 quoted_by INTEGER REFERENCES users(id), approved_by INTEGER REFERENCES users(id),
 completion_note TEXT, completion_document_id INTEGER REFERENCES documents(id),
 invoice_document_id INTEGER REFERENCES documents(id), completed_at TEXT,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE credit_notes (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), reason TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_by INTEGER NOT NULL REFERENCES users(id), reviewed_by INTEGER REFERENCES users(id), review_note TEXT,
 created_at TEXT NOT NULL, reviewed_at TEXT
);
CREATE TABLE payment_plans (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 title TEXT NOT NULL, total_cents INTEGER NOT NULL CHECK(total_cents>0), instalment_cents INTEGER NOT NULL CHECK(instalment_cents>0),
 first_due TEXT NOT NULL, interval_days INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE plan_instalments (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), plan_id INTEGER NOT NULL REFERENCES payment_plans(id),
 due_date TEXT NOT NULL, amount_cents INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'open',
 payment_proof_id INTEGER REFERENCES payment_proofs(id), paid_at TEXT
);
CREATE TABLE published_statements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), settlement_id INTEGER NOT NULL REFERENCES settlements(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 published_by INTEGER NOT NULL REFERENCES users(id), published_at TEXT NOT NULL,
 UNIQUE(settlement_id,contract_id)
);
CREATE TABLE job_runs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), run_on TEXT NOT NULL,
 created_at TEXT NOT NULL, result TEXT NOT NULL, UNIQUE(company_id,run_on)
);
CREATE INDEX portal_contract_access ON portal_accounts(user_id,tenant_id,enabled);
CREATE INDEX portal_messages_contract ON portal_messages(contract_id,id);
CREATE INDEX notification_inbox ON notifications(user_id,is_read);
CREATE INDEX work_order_contractor ON work_orders(contractor_id,status);
CREATE TRIGGER portal_message_no_update BEFORE UPDATE ON portal_messages BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
CREATE TRIGGER portal_message_no_delete BEFORE DELETE ON portal_messages BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
CREATE TRIGGER repair_update_no_update BEFORE UPDATE ON repair_updates BEGIN SELECT RAISE(ABORT,'Repair updates are immutable'); END;
CREATE TRIGGER repair_update_no_delete BEFORE DELETE ON repair_updates BEGIN SELECT RAISE(ABORT,'Repair updates are immutable'); END;
'''

V3 = '''
CREATE UNIQUE INDEX installment_one_payment ON plan_instalments(payment_proof_id) WHERE payment_proof_id IS NOT NULL;
CREATE INDEX invoice_period_company ON invoices(company_id,period);
CREATE INDEX proof_invoice_status ON payment_proofs(invoice_id,status);
CREATE INDEX task_due_company ON tasks(company_id,status,due_date);
CREATE INDEX repairs_company_status ON repairs(company_id,status,due_date);
CREATE TRIGGER credit_note_no_delete BEFORE DELETE ON credit_notes BEGIN SELECT RAISE(ABORT,'Credit notes are immutable'); END;
'''

V4 = '''
CREATE TABLE inspector_assignments (
 user_id INTEGER NOT NULL REFERENCES users(id), company_id INTEGER NOT NULL REFERENCES companies(id),
 property_id INTEGER NOT NULL REFERENCES properties(id), assigned_by INTEGER NOT NULL REFERENCES users(id),
 assigned_at TEXT NOT NULL, PRIMARY KEY(user_id,property_id)
);
CREATE INDEX inspector_property_access ON inspector_assignments(company_id,user_id,property_id);
'''

V5 = '''
CREATE TABLE contractor_agreement_submissions (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contractor_id INTEGER NOT NULL REFERENCES contractors(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')),
 submitted_at TEXT NOT NULL, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT, review_note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX contractor_agreement_review ON contractor_agreement_submissions(company_id,status,id);
'''

V6 = '''
CREATE TABLE arrears_cases (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL UNIQUE REFERENCES invoices(id),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','paused','resolved','closed')),
 paused_until TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE arrears_events (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), case_id INTEGER NOT NULL REFERENCES arrears_cases(id),
 step TEXT NOT NULL, occurred_on TEXT NOT NULL, received_on TEXT, note TEXT NOT NULL DEFAULT '',
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE INDEX arrears_cases_company_status ON arrears_cases(company_id,status);
CREATE INDEX arrears_events_case ON arrears_events(case_id,id);
'''

V7 = '''
ALTER TABLE portal_messages ADD COLUMN thread_id INTEGER REFERENCES portal_messages(id);
CREATE INDEX portal_messages_thread ON portal_messages(contract_id,thread_id,id);
'''

V8 = '''
CREATE TABLE outbound_email_jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 kind TEXT NOT NULL CHECK(kind IN ('overdue_friendly','overdue_followup')),
 recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, last_error TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, UNIQUE(invoice_id,kind)
);
CREATE INDEX outbound_email_queue ON outbound_email_jobs(status,next_attempt_at,id);
CREATE INDEX outbound_email_company ON outbound_email_jobs(company_id,invoice_id,id);
'''

V9 = '''
ALTER TABLE outbound_email_jobs RENAME TO outbound_email_jobs_v8;
CREATE TABLE outbound_email_jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 kind TEXT NOT NULL CHECK(kind IN ('invoice_issued','overdue_friendly','overdue_followup')),
 recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, last_error TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, UNIQUE(invoice_id,kind)
);
INSERT INTO outbound_email_jobs SELECT * FROM outbound_email_jobs_v8;
DROP TABLE outbound_email_jobs_v8;
CREATE INDEX outbound_email_queue ON outbound_email_jobs(status,next_attempt_at,id);
CREATE INDEX outbound_email_company ON outbound_email_jobs(company_id,invoice_id,id);
'''

V10 = '''
CREATE TABLE service_charge_groups (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE service_charge_group_units (
 group_id INTEGER NOT NULL REFERENCES service_charge_groups(id),
 property_id INTEGER NOT NULL REFERENCES properties(id), share_percent REAL NOT NULL DEFAULT 0 CHECK(share_percent>=0 AND share_percent<=100),
 PRIMARY KEY(group_id,property_id)
);
ALTER TABLE cost_lines ADD COLUMN service_group_id INTEGER REFERENCES service_charge_groups(id);
ALTER TABLE cost_lines ADD COLUMN allocation_method TEXT NOT NULL DEFAULT 'tenancy_days'
 CHECK(allocation_method IN ('tenancy_days','equal','floor_area','fixed_percentage'));
CREATE TABLE published_statements_backup AS SELECT * FROM published_statements;
DROP TABLE published_statements;
CREATE TABLE settlements_rebuild (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 year INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', snapshot TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), approved_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL,
 service_group_id INTEGER REFERENCES service_charge_groups(id)
);
INSERT INTO settlements_rebuild(id,company_id,property_id,year,status,snapshot,created_by,approved_by,created_at)
 SELECT id,company_id,property_id,year,status,snapshot,created_by,approved_by,created_at FROM settlements;
DROP TABLE settlements;
ALTER TABLE settlements_rebuild RENAME TO settlements;
CREATE UNIQUE INDEX settlement_property_year ON settlements(property_id,year) WHERE service_group_id IS NULL;
CREATE UNIQUE INDEX settlement_group_year ON settlements(service_group_id,year) WHERE service_group_id IS NOT NULL;
CREATE TABLE published_statements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), settlement_id INTEGER NOT NULL REFERENCES settlements(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), document_id INTEGER NOT NULL REFERENCES documents(id),
 published_by INTEGER NOT NULL REFERENCES users(id), published_at TEXT NOT NULL,
 UNIQUE(settlement_id,contract_id)
);
INSERT INTO published_statements SELECT * FROM published_statements_backup;
DROP TABLE published_statements_backup;
CREATE INDEX service_charge_group_company ON service_charge_groups(company_id,id);
CREATE INDEX service_charge_unit_property ON service_charge_group_units(property_id,group_id);
'''

V11 = '''
CREATE TABLE cost_lines_rebuild (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 year INTEGER NOT NULL, category TEXT NOT NULL, supplier TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
 document_id INTEGER REFERENCES documents(id), created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL,
 service_group_id INTEGER REFERENCES service_charge_groups(id),
 allocation_method TEXT NOT NULL DEFAULT 'tenancy_days' CHECK(allocation_method IN ('tenancy_days','equal','floor_area','fixed_percentage')),
 entry_method TEXT NOT NULL DEFAULT 'invoice' CHECK(entry_method IN ('invoice','manual','import'))
);
INSERT INTO cost_lines_rebuild(id,company_id,property_id,year,category,supplier,amount_cents,document_id,created_by,created_at,service_group_id,allocation_method,entry_method)
 SELECT id,company_id,property_id,year,category,supplier,amount_cents,document_id,created_by,created_at,service_group_id,allocation_method,'invoice' FROM cost_lines;
DROP TABLE cost_lines;
ALTER TABLE cost_lines_rebuild RENAME TO cost_lines;
'''

V12 = '''
CREATE TABLE contract_signature_requests (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 source_document_id INTEGER NOT NULL REFERENCES documents(id), completed_document_id INTEGER REFERENCES documents(id),
 envelope_id TEXT NOT NULL UNIQUE, status TEXT NOT NULL, tenant_email TEXT NOT NULL, landlord_email TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX signature_requests_contract ON contract_signature_requests(company_id,contract_id,id);
'''

V13 = '''
DROP TRIGGER IF EXISTS contract_identity_locked;
CREATE TRIGGER contract_identity_locked BEFORE UPDATE OF signed_on,company_id,property_id,tenant_id,start_date ON contracts
WHEN NOT (OLD.status='pending signature' AND NEW.status='active' AND NEW.company_id=OLD.company_id AND NEW.property_id=OLD.property_id AND NEW.tenant_id=OLD.tenant_id AND NEW.start_date=OLD.start_date AND NEW.end_date IS OLD.end_date)
BEGIN SELECT RAISE(ABORT,'Contract identity is locked'); END;
'''

V14 = '''
ALTER TABLE tenants ADD COLUMN archived_at TEXT;
'''


V15 = '''
CREATE TABLE owners (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', payout_details TEXT NOT NULL DEFAULT '',
 archived_at TEXT, created_at TEXT NOT NULL
);
CREATE TABLE portfolios (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), owner_id INTEGER NOT NULL REFERENCES owners(id),
 name TEXT NOT NULL, fee_percent REAL NOT NULL DEFAULT 0 CHECK(fee_percent>=0 AND fee_percent<=100), created_at TEXT NOT NULL,
 UNIQUE(company_id,name)
);
ALTER TABLE properties ADD COLUMN portfolio_id INTEGER REFERENCES portfolios(id);
CREATE INDEX properties_portfolio ON properties(portfolio_id);
CREATE TABLE owner_accounts (
 user_id INTEGER NOT NULL REFERENCES users(id), owner_id INTEGER NOT NULL UNIQUE REFERENCES owners(id),
 company_id INTEGER NOT NULL REFERENCES companies(id), enabled INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, PRIMARY KEY(user_id,owner_id)
);
CREATE TABLE owner_expenses (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
 property_id INTEGER REFERENCES properties(id), incurred_on TEXT NOT NULL, category TEXT NOT NULL, description TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>0), document_id INTEGER REFERENCES documents(id),
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE owner_statements (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
 period TEXT NOT NULL, collected_cents INTEGER NOT NULL, fee_cents INTEGER NOT NULL, expenses_cents INTEGER NOT NULL,
 net_cents INTEGER NOT NULL, snapshot TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','published','paid')),
 created_by INTEGER NOT NULL REFERENCES users(id), published_at TEXT, paid_on TEXT, payout_reference TEXT,
 paid_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(portfolio_id,period)
);
CREATE TRIGGER owner_statement_frozen BEFORE UPDATE OF collected_cents,fee_cents,expenses_cents,net_cents,snapshot,period,portfolio_id ON owner_statements BEGIN SELECT RAISE(ABORT,'Owner statement figures are frozen'); END;
ALTER TABLE account_invitations RENAME TO account_invitations_v14;
CREATE TABLE account_invitations (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), email TEXT NOT NULL,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('tenant','contractor','owner')),
 target_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
 used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
INSERT INTO account_invitations SELECT id,company_id,email,name,kind,target_id,token_hash,expires_at,used_at,revoked,created_by,created_at FROM account_invitations_v14;
DROP TABLE account_invitations_v14;
'''

V16 = '''
CREATE TABLE sla_policies (
 company_id INTEGER NOT NULL REFERENCES companies(id), urgency TEXT NOT NULL CHECK(urgency IN ('Emergency','Urgent','Routine')),
 response_hours INTEGER NOT NULL CHECK(response_hours BETWEEN 1 AND 720), resolve_days INTEGER NOT NULL CHECK(resolve_days BETWEEN 1 AND 90),
 PRIMARY KEY(company_id,urgency)
);
ALTER TABLE repairs ADD COLUMN response_due_at TEXT;
ALTER TABLE repairs ADD COLUMN responded_at TEXT;
CREATE TABLE automation_rules (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 trigger TEXT NOT NULL CHECK(trigger IN ('invoice_overdue','invoice_due_soon','contract_ending','repair_open','proof_pending')),
 days INTEGER NOT NULL CHECK(days BETWEEN 0 AND 365),
 action TEXT NOT NULL CHECK(action IN ('create_task','notify_staff','notify_tenant')),
 title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '', owner TEXT NOT NULL DEFAULT 'Property team',
 enabled INTEGER NOT NULL DEFAULT 1, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE automation_runs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), rule_id INTEGER NOT NULL REFERENCES automation_rules(id) ON DELETE CASCADE,
 entity TEXT NOT NULL, entity_id INTEGER NOT NULL, summary TEXT NOT NULL, ran_at TEXT NOT NULL, UNIQUE(rule_id,entity,entity_id)
);
CREATE TABLE custom_fields (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 entity TEXT NOT NULL CHECK(entity IN ('tenant','property','contract','owner')), key TEXT NOT NULL, label TEXT NOT NULL,
 field_type TEXT NOT NULL DEFAULT 'text' CHECK(field_type IN ('text','number','date')), created_at TEXT NOT NULL, UNIQUE(company_id,entity,key)
);
CREATE TABLE custom_values (
 company_id INTEGER NOT NULL REFERENCES companies(id), field_id INTEGER NOT NULL REFERENCES custom_fields(id) ON DELETE CASCADE,
 entity_id INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY(field_id,entity_id)
);
CREATE TABLE doc_templates (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), name TEXT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('Lease','Letter','Notice','Other')), body TEXT NOT NULL,
 archived INTEGER NOT NULL DEFAULT 0, created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(company_id,name)
);
CREATE TABLE owner_documents (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), owner_id INTEGER NOT NULL REFERENCES owners(id),
 document_id INTEGER NOT NULL REFERENCES documents(id), title TEXT NOT NULL, category TEXT NOT NULL,
 shared_by INTEGER NOT NULL REFERENCES users(id), from_owner INTEGER NOT NULL DEFAULT 0, shared_at TEXT NOT NULL,
 revoked INTEGER NOT NULL DEFAULT 0, UNIQUE(owner_id,document_id)
);
'''

V17 = '''
CREATE TABLE thread_states (
 thread_id INTEGER PRIMARY KEY REFERENCES portal_messages(id), company_id INTEGER NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')), assignee_id INTEGER REFERENCES users(id),
 updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL
);
CREATE TABLE canned_replies (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), title TEXT NOT NULL, body TEXT NOT NULL,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(company_id,title)
);
'''

V18 = '''
CREATE TABLE lease_offers (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), property_id INTEGER NOT NULL REFERENCES properties(id),
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), template_id INTEGER REFERENCES doc_templates(id),
 start_date TEXT NOT NULL, end_date TEXT, basic_cents INTEGER NOT NULL, service_cents INTEGER NOT NULL DEFAULT 0, deposit_cents INTEGER NOT NULL DEFAULT 0,
 body TEXT NOT NULL, document_id INTEGER REFERENCES documents(id),
 status TEXT NOT NULL DEFAULT 'sent' CHECK(status IN ('sent','signed','returned','approved','declined','withdrawn')),
 sent_by INTEGER REFERENCES users(id), sent_at TEXT NOT NULL,
 signer_name TEXT, signed_at TEXT, signed_ip TEXT, signed_hash TEXT,
 response_note TEXT, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT,
 contract_id INTEGER REFERENCES contracts(id), final_document_id INTEGER REFERENCES documents(id)
);
CREATE INDEX lease_offers_company ON lease_offers(company_id,status);
'''

V19 = '''
CREATE TABLE company_logos (
 company_id INTEGER PRIMARY KEY REFERENCES companies(id), mime TEXT NOT NULL, data BLOB NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE invoice_revisions (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 revision INTEGER NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL,
 edited_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(invoice_id,revision)
);
CREATE TRIGGER invoice_revision_no_update BEFORE UPDATE ON invoice_revisions BEGIN SELECT RAISE(ABORT,'Invoice revisions are immutable'); END;
CREATE TRIGGER invoice_revision_no_delete BEFORE DELETE ON invoice_revisions BEGIN SELECT RAISE(ABORT,'Invoice revisions are immutable'); END;
DROP TRIGGER invoice_no_update;
CREATE TRIGGER invoice_no_update BEFORE UPDATE ON invoices
WHEN NEW.id IS NOT OLD.id OR NEW.company_id IS NOT OLD.company_id OR NEW.contract_id IS NOT OLD.contract_id OR NEW.number IS NOT OLD.number OR NEW.period IS NOT OLD.period OR NEW.created_at IS NOT OLD.created_at
BEGIN SELECT RAISE(ABORT,'Issued invoices are immutable: only amounts, due date and lines can be revised, and every revision is kept'); END;
'''

V20 = '''
ALTER TABLE lease_offers ADD COLUMN form_fields TEXT;
ALTER TABLE lease_offers ADD COLUMN form_values TEXT;
ALTER TABLE lease_offers ADD COLUMN tenant_signature BLOB;
ALTER TABLE lease_offers ADD COLUMN tenant_signature_mode TEXT;
ALTER TABLE lease_offers ADD COLUMN landlord_signature BLOB;
ALTER TABLE lease_offers ADD COLUMN landlord_signature_mode TEXT;
ALTER TABLE lease_offers ADD COLUMN edited INTEGER NOT NULL DEFAULT 0;
'''


V21 = '''
CREATE TABLE company_signatures (
 company_id INTEGER PRIMARY KEY REFERENCES companies(id), signer_name TEXT NOT NULL, signer_title TEXT NOT NULL DEFAULT '',
 mode TEXT NOT NULL, data BLOB NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE lease_offers ADD COLUMN landlord_signer_name TEXT;
ALTER TABLE lease_offers ADD COLUMN landlord_signer_title TEXT;
ALTER TABLE lease_offers ADD COLUMN landlord_signed_at TEXT;
'''


V22 = '''
ALTER TABLE companies ADD COLUMN review_invoices INTEGER NOT NULL DEFAULT 0;
ALTER TABLE invoices ADD COLUMN released_at TEXT;
UPDATE invoices SET released_at=created_at;
'''


V23 = '''
CREATE TABLE staff_invites (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), email TEXT NOT NULL, name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','manager','finance','property','inspector')),
 token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL, used_at TEXT, revoked INTEGER NOT NULL DEFAULT 0,
 created_by INTEGER NOT NULL REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE INDEX staff_invites_company ON staff_invites(company_id,revoked,used_at);
'''


V24 = '''
ALTER TABLE portfolios ADD COLUMN archived_at TEXT;
'''


V25 = '''
ALTER TABLE memberships RENAME TO memberships_v24;
CREATE TABLE memberships (
 user_id INTEGER REFERENCES users(id), company_id INTEGER REFERENCES companies(id),
 role TEXT NOT NULL CHECK(role IN ('admin','manager','finance','property','inspector','landlord')), PRIMARY KEY(user_id,company_id)
);
INSERT INTO memberships SELECT user_id,company_id,role FROM memberships_v24;
DROP TABLE memberships_v24;
ALTER TABLE tenants ADD COLUMN scope_owner_id INTEGER REFERENCES owners(id);
'''


# V26: privacy erasure after the retention period, and indexes for the hot company/contract lookups.
# Messages and repair notes stay immutable, except while app/privacy.py anonymises a former tenant (a row in privacy_erasure).
V26 = '''
ALTER TABLE tenants ADD COLUMN erased_at TEXT;
CREATE TABLE privacy_erasure (active INTEGER NOT NULL);
DROP TRIGGER IF EXISTS portal_message_no_update;
CREATE TRIGGER portal_message_no_update BEFORE UPDATE ON portal_messages WHEN NOT EXISTS (SELECT 1 FROM privacy_erasure) BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
DROP TRIGGER IF EXISTS repair_update_no_update;
CREATE TRIGGER repair_update_no_update BEFORE UPDATE ON repair_updates WHEN NOT EXISTS (SELECT 1 FROM privacy_erasure) BEGIN SELECT RAISE(ABORT,'Repair updates are immutable'); END;
CREATE INDEX IF NOT EXISTS documents_company ON documents(company_id, contract_id);
CREATE INDEX IF NOT EXISTS payment_proofs_company ON payment_proofs(company_id, status);
CREATE INDEX IF NOT EXISTS portal_messages_company ON portal_messages(company_id, created_at);
CREATE INDEX IF NOT EXISTS communications_company ON communications(company_id, created_at);
CREATE INDEX IF NOT EXISTS requests_company ON requests(company_id, status);
CREATE INDEX IF NOT EXISTS repair_updates_repair ON repair_updates(repair_id);
CREATE INDEX IF NOT EXISTS ledger_contract ON ledger(contract_id);
CREATE INDEX IF NOT EXISTS tenants_email ON tenants(email);
'''


# V27: bank-statement import and matching, and the bank accounts tenants pay from (app/banking.py)
V27 = '''
CREATE TABLE tenant_bank_accounts (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 iban TEXT NOT NULL, holder TEXT NOT NULL DEFAULT '', source TEXT NOT NULL CHECK(source IN ('tenant','staff','learned')),
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(company_id,tenant_id,iban)
);
CREATE INDEX tenant_bank_accounts_iban ON tenant_bank_accounts(company_id, iban);
CREATE TABLE bank_imports (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), filename TEXT NOT NULL, format TEXT NOT NULL, sha256 TEXT NOT NULL,
 lines INTEGER NOT NULL DEFAULT 0, booked INTEGER NOT NULL DEFAULT 0, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL
);
CREATE TABLE bank_lines (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), import_id INTEGER NOT NULL REFERENCES bank_imports(id),
 dedupe TEXT NOT NULL, booked_on TEXT NOT NULL, amount_cents INTEGER NOT NULL, iban TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
 description TEXT NOT NULL DEFAULT '', bank_ref TEXT NOT NULL DEFAULT '',
 status TEXT NOT NULL CHECK(status IN ('booked','suggested','unmatched','ignored')), tenant_id INTEGER REFERENCES tenants(id), invoice_id INTEGER REFERENCES invoices(id),
 reason TEXT, decided_by INTEGER REFERENCES users(id), decided_at TEXT, created_at TEXT NOT NULL, UNIQUE(company_id,dedupe)
);
CREATE INDEX bank_lines_status ON bank_lines(company_id, status);
CREATE TABLE bank_bookings (line_id INTEGER NOT NULL REFERENCES bank_lines(id), ledger_id INTEGER NOT NULL UNIQUE REFERENCES ledger(id));
'''


# V28: landlords who collect the rent into their own bank account, and tenants who report "I have paid"
V28 = '''
ALTER TABLE owners ADD COLUMN collects_rent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bank_imports ADD COLUMN owner_id INTEGER REFERENCES owners(id);
ALTER TABLE bank_lines ADD COLUMN owner_id INTEGER REFERENCES owners(id);
CREATE TABLE payment_notices (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 invoice_id INTEGER NOT NULL REFERENCES invoices(id), amount_cents INTEGER NOT NULL CHECK(amount_cents>0), paid_on TEXT NOT NULL,
 iban TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'waiting' CHECK(status IN ('waiting','confirmed','withdrawn')),
 bank_line_id INTEGER REFERENCES bank_lines(id), created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, confirmed_at TEXT
);
CREATE INDEX payment_notices_invoice ON payment_notices(invoice_id, status);
'''


# V29: choose how bank matches are handled (automatic, or review first), and keep the statement file for the marked PDF
V29 = '''
ALTER TABLE companies ADD COLUMN bank_mode TEXT CHECK(bank_mode IN ('auto','review'));
ALTER TABLE owners ADD COLUMN bank_mode TEXT CHECK(bank_mode IN ('auto','review'));
ALTER TABLE bank_imports ADD COLUMN mode TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE bank_lines ADD COLUMN certain INTEGER NOT NULL DEFAULT 0;
'''


# V30: every ticket has a priority. Repairs keep theirs in repairs.urgency; requests and conversations get one here.
# Values for all tickets: Emergency, Urgent, Routine (shown as Normal) and Low.
V30 = '''
ALTER TABLE requests ADD COLUMN priority TEXT NOT NULL DEFAULT 'Routine' CHECK(priority IN ('Emergency','Urgent','Routine','Low'));
ALTER TABLE thread_states ADD COLUMN priority TEXT NOT NULL DEFAULT 'Routine' CHECK(priority IN ('Emergency','Urgent','Routine','Low'));
'''


# V31: a tenant's video with a written request or complaint
V31 = '''
ALTER TABLE requests ADD COLUMN video_document_id INTEGER REFERENCES documents(id);
'''


# V32: one-time links to set a new password (app/password_reset.py)
V32 = '''
CREATE TABLE password_resets (
 id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), token_hash TEXT NOT NULL UNIQUE, expires_at TEXT NOT NULL,
 used_at TEXT, created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, ip TEXT NOT NULL DEFAULT ''
);
CREATE INDEX password_resets_user ON password_resets(user_id, created_at);
CREATE INDEX password_resets_ip ON password_resets(ip, created_at);
'''


# V33: service agreements sent to contractors and signed electronically (app/contractor_agreements.py)
V33 = '''
CREATE TABLE contractor_offers (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contractor_id INTEGER NOT NULL REFERENCES contractors(id),
 body TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT,
 hourly_cents INTEGER NOT NULL, callout_cents INTEGER NOT NULL DEFAULT 0, emergency_percent INTEGER NOT NULL DEFAULT 0,
 quote_threshold_cents INTEGER NOT NULL DEFAULT 0, payment_days INTEGER NOT NULL DEFAULT 30, warranty_months INTEGER NOT NULL DEFAULT 6,
 status TEXT NOT NULL CHECK(status IN ('sent','returned','signed','approved','declined','withdrawn')),
 sent_by INTEGER REFERENCES users(id), sent_at TEXT NOT NULL,
 client_signature BLOB, client_signature_mode TEXT, client_signer_name TEXT, client_signer_title TEXT, client_signed_at TEXT,
 form_values TEXT, signer_name TEXT, signed_at TEXT, signed_ip TEXT, signed_hash TEXT, contractor_signature BLOB, contractor_signature_mode TEXT,
 response_note TEXT, reviewed_by INTEGER REFERENCES users(id), reviewed_at TEXT, final_document_id INTEGER REFERENCES documents(id), created_at TEXT NOT NULL
);
CREATE INDEX contractor_offers_contractor ON contractor_offers(contractor_id, status);
'''

# V34: co-tenants (medehuurders, e.g. spouses) on one contract, an optional split of the rent, and who paid each payment.
# The contract's tenant stays locked, except for a recorded handover to a co-tenant who is already on the contract.
V34 = '''
CREATE TABLE contract_tenants (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), share_cents INTEGER, added_by INTEGER REFERENCES users(id), added_at TEXT NOT NULL,
 removed_at TEXT, removed_by INTEGER REFERENCES users(id), removed_reason TEXT, UNIQUE(contract_id, tenant_id)
);
CREATE INDEX contract_tenants_tenant ON contract_tenants(tenant_id);
CREATE VIEW contract_people AS
 SELECT c.id contract_id, c.company_id, c.tenant_id, 1 main FROM contracts c
 UNION ALL SELECT ct.contract_id, ct.company_id, ct.tenant_id, 0 FROM contract_tenants ct WHERE ct.removed_at IS NULL;
ALTER TABLE contracts ADD COLUMN split_mode TEXT NOT NULL DEFAULT '';
ALTER TABLE ledger ADD COLUMN payer_tenant_id INTEGER REFERENCES tenants(id);
ALTER TABLE payment_notices ADD COLUMN tenant_id INTEGER REFERENCES tenants(id);
UPDATE payment_notices SET tenant_id=(SELECT tenant_id FROM contracts WHERE id=payment_notices.contract_id);
CREATE TABLE contract_handovers (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), contract_id INTEGER NOT NULL REFERENCES contracts(id),
 from_tenant_id INTEGER NOT NULL REFERENCES tenants(id), to_tenant_id INTEGER NOT NULL REFERENCES tenants(id),
 created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, applied INTEGER NOT NULL DEFAULT 0
);
DROP TRIGGER IF EXISTS contract_identity_locked;
CREATE TRIGGER contract_identity_locked BEFORE UPDATE OF signed_on,company_id,property_id,tenant_id,start_date ON contracts
WHEN NOT ((OLD.status='pending signature' AND NEW.status='active' AND NEW.company_id=OLD.company_id AND NEW.property_id=OLD.property_id AND NEW.tenant_id=OLD.tenant_id AND NEW.start_date=OLD.start_date AND NEW.end_date IS OLD.end_date)
 OR (NEW.signed_on IS OLD.signed_on AND NEW.company_id=OLD.company_id AND NEW.property_id=OLD.property_id AND NEW.start_date=OLD.start_date
     AND EXISTS (SELECT 1 FROM contract_handovers h WHERE h.contract_id=OLD.id AND h.from_tenant_id=OLD.tenant_id AND h.to_tenant_id=NEW.tenant_id AND h.applied=0)))
BEGIN SELECT RAISE(ABORT,'Contract identity is locked'); END;
'''

# V35: co-tenants who sign the tenancy agreement together with the tenant (each in their own portal), and the agreed split
V35 = '''
CREATE TABLE lease_offer_signers (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), offer_id INTEGER NOT NULL REFERENCES lease_offers(id),
 tenant_id INTEGER NOT NULL REFERENCES tenants(id), share_cents INTEGER, signer_name TEXT, signed_at TEXT, signed_ip TEXT, signed_hash TEXT,
 signature BLOB, signature_mode TEXT, form_values TEXT, UNIQUE(offer_id, tenant_id)
);
ALTER TABLE lease_offers ADD COLUMN split_mode TEXT NOT NULL DEFAULT '';
'''

# V40: platform-wide settings the developer can change, such as the software's name (app/brand.py)
# V42: when the person last did something; a session without activity for KREIN_IDLE_MINUTES ends (app/security.py)
V42 = '''
ALTER TABLE sessions ADD COLUMN last_active TEXT;
'''

# V44: tenants send their details through the company website's form (app/website.py); staff enter them as a tenant.
# A tenant's bank account also keeps the bank's name.
V44 = '''
CREATE TABLE tenant_registrations (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','registered','declined')), language TEXT NOT NULL DEFAULT 'nl',
 name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', home_address TEXT NOT NULL DEFAULT '',
 co_name TEXT NOT NULL DEFAULT '', co_email TEXT NOT NULL DEFAULT '', co_phone TEXT NOT NULL DEFAULT '', co_language TEXT NOT NULL DEFAULT 'nl',
 iban TEXT NOT NULL, account_holder TEXT NOT NULL, bank_name TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
 consent_at TEXT NOT NULL, created_at TEXT NOT NULL, handled_by INTEGER REFERENCES users(id), handled_at TEXT, tenant_id INTEGER REFERENCES tenants(id)
);
CREATE INDEX tenant_registrations_company ON tenant_registrations(company_id,status,id);
ALTER TABLE tenant_bank_accounts ADD COLUMN bank_name TEXT NOT NULL DEFAULT '';
'''

# V45: property owners ask for an introduction through the company website (/kennismaking); staff follow up.
V45 = '''
CREATE TABLE owner_enquiries (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','contacted','closed')), language TEXT NOT NULL DEFAULT 'nl',
 name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, location TEXT NOT NULL,
 property_count TEXT NOT NULL, property_type TEXT NOT NULL, situation TEXT NOT NULL DEFAULT '', services TEXT NOT NULL,
 message TEXT NOT NULL DEFAULT '', source_page TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 consent_at TEXT NOT NULL, created_at TEXT NOT NULL, handled_by INTEGER REFERENCES users(id), handled_at TEXT
);
CREATE INDEX owner_enquiries_company ON owner_enquiries(company_id,status,id);
'''

# V46: a tenant's postal address (Dutch format) when mail should not go to the rented home (app/tenant_address.py)
V46 = '''
ALTER TABLE tenants ADD COLUMN addr_street TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_number TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_addition TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_postcode TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_city TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_country TEXT NOT NULL DEFAULT 'NL';
'''

# V43: the developer may delete a tenant permanently (app/developer.py). The financial records still refuse every delete,
# except inside that one transaction (a row in record_deletion); changing them stays impossible.
V43 = '''
CREATE TABLE record_deletion (active INTEGER NOT NULL);
DROP TRIGGER IF EXISTS ledger_no_delete;
CREATE TRIGGER ledger_no_delete BEFORE DELETE ON ledger WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Ledger entries are immutable'); END;
DROP TRIGGER IF EXISTS invoice_no_delete;
CREATE TRIGGER invoice_no_delete BEFORE DELETE ON invoices WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Issued invoices are immutable'); END;
DROP TRIGGER IF EXISTS rent_no_delete;
CREATE TRIGGER rent_no_delete BEFORE DELETE ON rent_versions WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Rent history is immutable'); END;
DROP TRIGGER IF EXISTS portal_message_no_delete;
CREATE TRIGGER portal_message_no_delete BEFORE DELETE ON portal_messages WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Messages are immutable'); END;
DROP TRIGGER IF EXISTS repair_update_no_delete;
CREATE TRIGGER repair_update_no_delete BEFORE DELETE ON repair_updates WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Repair updates are immutable'); END;
DROP TRIGGER IF EXISTS credit_note_no_delete;
CREATE TRIGGER credit_note_no_delete BEFORE DELETE ON credit_notes WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Credit notes are immutable'); END;
DROP TRIGGER IF EXISTS invoice_revision_no_delete;
CREATE TRIGGER invoice_revision_no_delete BEFORE DELETE ON invoice_revisions WHEN NOT EXISTS (SELECT 1 FROM record_deletion) BEGIN SELECT RAISE(ABORT,'Invoice revisions are immutable'); END;
'''

# V41: a pending invitation keeps its link (cleared once it is used or replaced) and whether it was emailed (app/portals.py)
V41 = '''
ALTER TABLE account_invitations ADD COLUMN token TEXT;
ALTER TABLE account_invitations ADD COLUMN email_status TEXT;
ALTER TABLE account_invitations ADD COLUMN email_error TEXT;
ALTER TABLE account_invitations ADD COLUMN emailed_at TEXT;
'''

V40 = '''
CREATE TABLE platform_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL
);
'''

# V39: the Developer's access matrix: a feature switched off for a role (no row = switched on, as before) (app/access.py)
V39 = '''
CREATE TABLE role_access (
 feature TEXT NOT NULL, role TEXT NOT NULL, allowed INTEGER NOT NULL DEFAULT 1, updated_by INTEGER REFERENCES users(id), updated_at TEXT NOT NULL,
 PRIMARY KEY(feature, role)
);
'''

# V38: the automatic bank connection (Ponto, read-only): the company's permission and the accounts it covers (app/ponto.py)
V38 = '''
CREATE TABLE bank_connections (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id), provider TEXT NOT NULL DEFAULT 'ponto',
 status TEXT NOT NULL CHECK(status IN ('pending','connected','expired','revoked','replaced')), oauth_state TEXT, code_verifier TEXT,
 access_token TEXT, refresh_token TEXT, access_expires_at TEXT, connected_by INTEGER REFERENCES users(id), connected_at TEXT,
 last_sync_at TEXT, last_error TEXT, reminded_at TEXT, created_at TEXT NOT NULL
);
CREATE INDEX bank_connections_company ON bank_connections(company_id,status);
CREATE TABLE bank_connection_accounts (
 id INTEGER PRIMARY KEY, connection_id INTEGER NOT NULL REFERENCES bank_connections(id), company_id INTEGER NOT NULL REFERENCES companies(id),
 ponto_id TEXT NOT NULL, iban TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '', holder TEXT NOT NULL DEFAULT '', authorization_expires_at TEXT,
 last_synced_at TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, UNIQUE(connection_id, ponto_id)
);
'''

# V37: invoice and reminder emails go to every person on the contract separately (one job per recipient), each with their own part
V37 = '''
ALTER TABLE outbound_email_jobs RENAME TO outbound_email_jobs_v36;
CREATE TABLE outbound_email_jobs (
 id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(id),
 contract_id INTEGER NOT NULL REFERENCES contracts(id), invoice_id INTEGER NOT NULL REFERENCES invoices(id),
 kind TEXT NOT NULL CHECK(kind IN ('invoice_issued','overdue_friendly','overdue_followup')),
 recipient TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sending','sent','failed','cancelled')),
 attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, last_error TEXT,
 created_at TEXT NOT NULL, sent_at TEXT, tenant_id INTEGER REFERENCES tenants(id), UNIQUE(invoice_id,kind,recipient)
);
INSERT INTO outbound_email_jobs(id,company_id,contract_id,invoice_id,kind,recipient,status,attempts,next_attempt_at,last_error,created_at,sent_at)
 SELECT id,company_id,contract_id,invoice_id,kind,recipient,status,attempts,next_attempt_at,last_error,created_at,sent_at FROM outbound_email_jobs_v36;
DROP TABLE outbound_email_jobs_v36;
CREATE INDEX outbound_email_queue ON outbound_email_jobs(status,next_attempt_at,id);
CREATE INDEX outbound_email_company ON outbound_email_jobs(company_id,invoice_id,id);
UPDATE outbound_email_jobs SET tenant_id=(SELECT tenant_id FROM contracts WHERE id=outbound_email_jobs.contract_id);
INSERT OR IGNORE INTO outbound_email_jobs(company_id,contract_id,invoice_id,kind,recipient,status,attempts,next_attempt_at,created_at,tenant_id)
 SELECT j.company_id,j.contract_id,j.invoice_id,j.kind,t.email,'queued',0,j.next_attempt_at,j.created_at,t.id FROM outbound_email_jobs j
 JOIN contract_tenants ct ON ct.contract_id=j.contract_id AND ct.removed_at IS NULL JOIN tenants t ON t.id=ct.tenant_id
 WHERE j.status IN ('queued','sending') AND t.archived_at IS NULL AND t.email LIKE '%_@_%';
'''

# V36: a partner or spouse added together with the tenant (before there is a contract): the household they rent as
V36 = '''
ALTER TABLE tenants ADD COLUMN co_of INTEGER REFERENCES tenants(id);
CREATE INDEX tenants_co_of ON tenants(co_of);
'''


# PostgreSQL (DATABASE_URL): app/schema_postgres.sql already contains every migration up to POSTGRES_BASELINE. A later
# migration is added twice: as V43 (and so on) above for SQLite, and here in PostgreSQL syntax under the same number.
POSTGRES_BASELINE = 42
POSTGRES = {
    43: '''
CREATE TABLE record_deletion (active BIGINT NOT NULL);
CREATE FUNCTION krein_refuse_unless_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' AND EXISTS (SELECT 1 FROM record_deletion) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '%', TG_ARGV[0] USING ERRCODE = '23000';
END $$;
CREATE OR REPLACE TRIGGER ledger_no_change BEFORE UPDATE OR DELETE ON ledger FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Ledger entries are immutable');
CREATE OR REPLACE TRIGGER invoice_no_delete BEFORE DELETE ON invoices FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Issued invoices are immutable');
CREATE OR REPLACE TRIGGER rent_no_change BEFORE UPDATE OR DELETE ON rent_versions FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Rent history is immutable');
CREATE OR REPLACE TRIGGER portal_message_no_delete BEFORE DELETE ON portal_messages FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Messages are immutable');
CREATE OR REPLACE TRIGGER repair_update_no_delete BEFORE DELETE ON repair_updates FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Repair updates are immutable');
CREATE OR REPLACE TRIGGER credit_note_no_delete BEFORE DELETE ON credit_notes FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Credit notes are immutable');
CREATE OR REPLACE TRIGGER invoice_revision_no_change BEFORE UPDATE OR DELETE ON invoice_revisions FOR EACH ROW EXECUTE FUNCTION krein_refuse_unless_deletion('Invoice revisions are immutable');
''',
    44: '''
CREATE TABLE tenant_registrations (
 id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, company_id BIGINT NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','registered','declined')), language TEXT NOT NULL DEFAULT 'nl',
 name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '', home_address TEXT NOT NULL DEFAULT '',
 co_name TEXT NOT NULL DEFAULT '', co_email TEXT NOT NULL DEFAULT '', co_phone TEXT NOT NULL DEFAULT '', co_language TEXT NOT NULL DEFAULT 'nl',
 iban TEXT NOT NULL, account_holder TEXT NOT NULL, bank_name TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
 consent_at TEXT NOT NULL, created_at TEXT NOT NULL, handled_by BIGINT REFERENCES users(id), handled_at TEXT, tenant_id BIGINT REFERENCES tenants(id)
);
CREATE INDEX tenant_registrations_company ON tenant_registrations(company_id,status,id);
ALTER TABLE tenant_bank_accounts ADD COLUMN bank_name TEXT NOT NULL DEFAULT '';
''',
    45: '''
CREATE TABLE owner_enquiries (
 id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, company_id BIGINT NOT NULL REFERENCES companies(id),
 status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','contacted','closed')), language TEXT NOT NULL DEFAULT 'nl',
 name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT NOT NULL, location TEXT NOT NULL,
 property_count TEXT NOT NULL, property_type TEXT NOT NULL, situation TEXT NOT NULL DEFAULT '', services TEXT NOT NULL,
 message TEXT NOT NULL DEFAULT '', source_page TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
 consent_at TEXT NOT NULL, created_at TEXT NOT NULL, handled_by BIGINT REFERENCES users(id), handled_at TEXT
);
CREATE INDEX owner_enquiries_company ON owner_enquiries(company_id,status,id);
''',
    46: '''
ALTER TABLE tenants ADD COLUMN addr_street TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_number TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_addition TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_postcode TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_city TEXT NOT NULL DEFAULT '';
ALTER TABLE tenants ADD COLUMN addr_country TEXT NOT NULL DEFAULT 'NL';
''',
}


def migrate_postgres(conn):
    from .db import now
    conn.execute('BEGIN IMMEDIATE')
    done = {r[0] for r in conn.execute('SELECT version FROM schema_migrations')}
    for version in sorted(POSTGRES):
        if version not in done:
            conn.executescript(POSTGRES[version])
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (version, now()))
    conn.commit()


def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)')
    # Execute each statement (including multi-line triggers) inside one transaction.
    import sqlite3
    from .db import now
    conn.execute('BEGIN IMMEDIATE')
    try:
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=2').fetchone():
            statement = ''
            for line in V2.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (2, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=3').fetchone():
            statement = ''
            for line in V3.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (3, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=4').fetchone():
            statement = ''
            for line in V4.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (4, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=5').fetchone():
            statement = ''
            for line in V5.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (5, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=6').fetchone():
            statement = ''
            for line in V6.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (6, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=7').fetchone():
            statement = ''
            for line in V7.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (7, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=8').fetchone():
            statement = ''
            for line in V8.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (8, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=9').fetchone():
            statement = ''
            for line in V9.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (9, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=10').fetchone():
            statement = ''
            for line in V10.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (10, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=11').fetchone():
            statement = ''
            for line in V11.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (11, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=12').fetchone():
            statement = ''
            for line in V12.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (12, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=13').fetchone():
            statement = ''
            for line in V13.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (13, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=14').fetchone():
            statement = ''
            for line in V14.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (14, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=15').fetchone():
            statement = ''
            for line in V15.splitlines():
                statement += line + '\n'
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (15, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=16').fetchone():
            statement = ''
            for line in V16.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (16, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=17').fetchone():
            statement = ''
            for line in V17.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (17, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=18').fetchone():
            statement = ''
            for line in V18.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (18, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=19').fetchone():
            statement = ''
            for line in V19.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (19, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=20').fetchone():
            statement = ''
            for line in V20.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (20, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=21').fetchone():
            statement = ''
            for line in V21.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (21, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=22').fetchone():
            statement = ''
            for line in V22.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (22, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=23').fetchone():
            statement = ''
            for line in V23.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (23, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=24').fetchone():
            statement = ''
            for line in V24.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (24, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=25').fetchone():
            statement = ''
            for line in V25.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (25, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=26').fetchone():
            statement = ''
            for line in V26.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (26, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=27').fetchone():
            statement = ''
            for line in V27.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (27, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=28').fetchone():
            statement = ''
            for line in V28.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (28, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=29').fetchone():
            statement = ''
            for line in V29.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (29, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=30').fetchone():
            statement = ''
            for line in V30.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (30, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=31').fetchone():
            statement = ''
            for line in V31.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (31, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=32').fetchone():
            statement = ''
            for line in V32.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (32, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=33').fetchone():
            statement = ''
            for line in V33.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (33, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=34').fetchone():
            statement = ''
            for line in V34.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (34, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=35').fetchone():
            statement = ''
            for line in V35.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (35, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=36').fetchone():
            statement = ''
            for line in V36.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (36, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=37').fetchone():
            statement = ''
            for line in V37.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (37, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=38').fetchone():
            statement = ''
            for line in V38.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (38, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=39').fetchone():
            statement = ''
            for line in V39.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (39, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=40').fetchone():
            statement = ''
            for line in V40.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (40, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=41').fetchone():
            statement = ''
            for line in V41.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (41, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=42').fetchone():
            statement = ''
            for line in V42.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (42, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=43').fetchone():
            statement = ''
            for line in V43.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (43, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=44').fetchone():
            statement = ''
            for line in V44.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (44, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=45').fetchone():
            statement = ''
            for line in V45.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (45, now()))
        if not conn.execute('SELECT 1 FROM schema_migrations WHERE version=46').fetchone():
            statement = ''
            for line in V46.splitlines():
                statement += line + chr(10)
                if sqlite3.complete_statement(statement):
                    conn.execute(statement)
                    statement = ''
            conn.execute('INSERT INTO schema_migrations VALUES(?,?)', (46, now()))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
