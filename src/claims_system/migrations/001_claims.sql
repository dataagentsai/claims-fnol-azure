-- The claims system's own tables (Tier 2). Plain SQL, applied once, in order,
-- by `claims_system.store.migrate`, which records each file in schema_migrations.
--
-- The fields are the AOAS entities' (motor-claims-fnol.aoas.yaml §2), with two
-- realisations of this system's own:
--   - `claim.incident_on` is stored and `incident_days_ago` derived from it, so
--     the age advances with the calendar (`advances: days`);
--   - `policy.payout_account_open` is whether the account on the policy can
--     still receive money. The AOAS leaves that to the claims system; it is what
--     makes `issue_payout` answer `kind: declined` (P-PAYOUT-DECLINED).

CREATE TABLE policyholder (
    id    text PRIMARY KEY,
    name  text NOT NULL,
    email text NOT NULL,
    phone text NOT NULL
);

CREATE TABLE policy (
    id                    text PRIMARY KEY CHECK (id ~ '^POL-[0-9]{6}$'),
    policyholder_id       text NOT NULL REFERENCES policyholder (id),
    product               text NOT NULL CHECK (product IN ('comprehensive', 'third_party')),
    status                text NOT NULL CHECK (status IN ('active', 'lapsed', 'cancelled')),
    vehicle_reg           text NOT NULL,
    excess                bigint NOT NULL CHECK (excess >= 0),
    payout_account_last4  text NOT NULL,
    payout_account_open   boolean NOT NULL DEFAULT true
);

CREATE TABLE claim (
    id                 text PRIMARY KEY CHECK (id ~ '^CLM-[0-9]{6}$'),
    policy_id          text NOT NULL REFERENCES policy (id),
    policyholder_id    text NOT NULL REFERENCES policyholder (id),
    status             text NOT NULL CHECK (status IN ('registered', 'documents_pending',
                           'under_assessment', 'approved', 'rejected', 'paid', 'withdrawn')),
    incident_type      text NOT NULL CHECK (incident_type IN ('collision', 'theft', 'fire',
                           'flood', 'glass', 'vandalism')),
    incident_on        date NOT NULL,
    injuries           boolean NOT NULL DEFAULT false,
    documents_missing  integer NOT NULL DEFAULT 0 CHECK (documents_missing >= 0),
    approved_amount    bigint NOT NULL DEFAULT 0 CHECK (approved_amount >= 0),
    note               text NOT NULL DEFAULT '',
    -- The AOAS invariants, held where the rows are.
    CONSTRAINT an_approved_amount_implies_an_assessment
        CHECK (approved_amount = 0 OR status IN ('approved', 'paid')),
    CONSTRAINT documents_pending_is_missing_one
        CHECK (status <> 'documents_pending' OR documents_missing >= 1)
);

CREATE INDEX claim_by_holder ON claim (policyholder_id);
CREATE INDEX claim_by_policy_incident ON claim (policy_id, incident_type);

-- A claim's number: CLM- and the next of this sequence, six digits.
CREATE SEQUENCE claim_number MINVALUE 1;

-- Documents attached to a claim, by type. One of each type: sending the same
-- type twice is the same document (`submit_document.identity`).
CREATE TABLE claim_document (
    claim_id       text NOT NULL REFERENCES claim (id),
    document_type  text NOT NULL,
    submitted_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (claim_id, document_type)
);

-- Every keyed write's first answer (AOAS `external.claims_system.idempotency`).
-- Written in the same transaction as the effect, so a retry after a lost reply
-- reads the answer instead of landing the effect twice. Keyed under the
-- policyholder, so one caller's key can never read another's answer.
CREATE TABLE answered (
    policyholder_id  text NOT NULL,
    key              text NOT NULL,
    operation        text NOT NULL,
    answer           jsonb NOT NULL,
    answered_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (policyholder_id, key)
);
