import { useEffect, useState } from "react";
import { api } from "./api";
import { PageHeader, Panel, StatusBadge } from "./ui";

function Field({
  label,
  auto,
  span,
  children,
}: {
  label: string;
  auto?: boolean;
  span?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={`field${span ? " span-2" : ""}`}>
      <label>
        {label}
        {auto ? <span className="auto"> auto</span> : null}
      </label>
      {children}
    </div>
  );
}

function nowParts() {
  const d = new Date();
  const month = d.toLocaleString("en-US", { month: "long" });
  const day = String(d.getDate());
  const year = String(d.getFullYear());
  let hours = d.getHours();
  const ampm = hours >= 12 ? "PM" : "AM";
  hours = hours % 12 || 12;
  const minutes = String(d.getMinutes()).padStart(2, "0");
  return { month, day, year, time: `${hours}:${minutes}`, ampm, stamp: d.toLocaleString() };
}

export function CitationForm() {
  const [catalog, setCatalog] = useState<any>({ agencies: [], courts: [], charges: [] });
  const [officer, setOfficer] = useState<any>({});
  const [subject, setSubject] = useState("");
  const [fine, setFine] = useState("0");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [list, setList] = useState<any[]>([]);
  const n = nowParts();
  const [form, setForm] = useState<any>({
    agency: "",
    county: "WYTHE",
    court_type: "General District Court (Traffic)",
    court_month: n.month,
    court_day: n.day,
    court_year: n.year,
    court_time: n.time,
    am_pm: n.ampm,
    va_code: "",
    local_code: "",
    charge_description: "",
    commercial_motor_vehicle: "No",
    hazardous_materials: "No",
    resulted_in_fatality: "No",
    highway_safety_corridor: "No",
    issued_by: "",
  });
  const set = (k: string, v: string) => setForm((f: any) => ({ ...f, [k]: v }));
  useEffect(() => {
    api("/api/catalog").then(setCatalog);
    api("/api/officer/profile").then((p: any) => {
      setOfficer(p);
      const key = sessionStorage.getItem("cad_dept");
      const d = (p.departments || []).find((x: any) => x.key === key);
      const agency = d?.agency || p.agency || "";
      const issued = [p.name, p.callsign, agency].filter(Boolean).join(" ");
      setForm((f: any) => ({
        ...f,
        agency: f.agency || agency,
        issued_by: issued || f.issued_by,
      }));
    });
    api<{ results: any[] }>("/api/citations").then((r) => setList(r.results || [])).catch(() => {});
  }, []);
  return (
    <div className="stack">
      <PageHeader
        kicker="Records"
        title="Virginia Uniform Summons"
        actions={
          <button type="button" onClick={() => window.print()}>
            Print
          </button>
        }
      />
      <div className="legal-doc">
        <h2>VIRGINIA UNIFORM SUMMONS</h2>
        <p className="muted" style={{ color: "#5c5346" }}>
          Roleplay record. Not an official Commonwealth document.
        </p>
        <h3>Agency</h3>
        <div className="form-grid">
          <Field label="Agency" auto>
            <select value={form.agency} onChange={(e) => set("agency", e.target.value)}>
              <option value="">Select agency</option>
              {(catalog.agencies || []).map((a: string) => (
                <option key={a}>{a}</option>
              ))}
            </select>
          </Field>
          <Field label="Subject Discord ID">
            <input value={subject} onChange={(e) => setSubject(e.target.value)} required />
          </Field>
        </div>
        <h3>Court and jurisdiction</h3>
        <div className="form-grid">
          <Field label="You are summoned to appear in the county of">
            <input value={form.county} onChange={(e) => set("county", e.target.value)} />
          </Field>
          <Field label="Court type">
            <select value={form.court_type} onChange={(e) => set("court_type", e.target.value)}>
              {(catalog.courts || []).map((c: string) => (
                <option key={c}>{c}</option>
              ))}
            </select>
          </Field>
        </div>
        <h3>Court appearance</h3>
        <div className="form-grid">
          <Field label="On (month)">
            <input value={form.court_month} onChange={(e) => set("court_month", e.target.value)} />
          </Field>
          <Field label="Day">
            <input value={form.court_day} onChange={(e) => set("court_day", e.target.value)} />
          </Field>
          <Field label="Year">
            <input value={form.court_year} onChange={(e) => set("court_year", e.target.value)} />
          </Field>
          <Field label="At (time)">
            <input value={form.court_time} onChange={(e) => set("court_time", e.target.value)} />
          </Field>
          <Field label="AM / PM">
            <select value={form.am_pm} onChange={(e) => set("am_pm", e.target.value)}>
              <option>AM</option>
              <option>PM</option>
            </select>
          </Field>
        </div>
        <h3>Charge information</h3>
        <div className="form-grid">
          <Field label="VA Code">
            <input
              list="va-codes"
              value={form.va_code}
              onChange={(e) => {
                const code = e.target.value;
                const hit = (catalog.charges || []).find((c: any) => c.code === code);
                setForm((f: any) => ({
                  ...f,
                  va_code: code,
                  charge_description: hit?.description || f.charge_description,
                }));
              }}
            />
            <datalist id="va-codes">
              {(catalog.charges || []).map((c: any) => (
                <option key={c.code} value={c.code}>
                  {c.description}
                </option>
              ))}
            </datalist>
          </Field>
          <Field label="Local code">
            <input value={form.local_code} onChange={(e) => set("local_code", e.target.value)} />
          </Field>
          <Field label="Describe charge" span>
            <textarea value={form.charge_description} onChange={(e) => set("charge_description", e.target.value)} />
          </Field>
          <Field label="Fine">
            <input value={fine} onChange={(e) => setFine(e.target.value)} />
          </Field>
        </div>
        <h3>Additional questions</h3>
        <div className="form-grid">
          {["commercial_motor_vehicle", "hazardous_materials", "resulted_in_fatality", "highway_safety_corridor"].map((k) => (
            <Field key={k} label={k.replaceAll("_", " ")}>
              <select value={form[k]} onChange={(e) => set(k, e.target.value)}>
                <option>No</option>
                <option>Yes</option>
              </select>
            </Field>
          ))}
        </div>
        <h3>Issuing officer</h3>
        <div className="form-grid">
          <Field label="Issued by" auto span>
            <input value={form.issued_by} onChange={(e) => set("issued_by", e.target.value)} />
          </Field>
          <Field label="Case no." auto>
            <input value="Assigned on save (0000+)" disabled />
          </Field>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {msg && <p>{msg}</p>}
      <div className="toolbar">
        <button
          className="primary"
          type="button"
          onClick={async () => {
            setErr("");
            if (!subject || !form.charge_description) {
              setErr("Subject Discord ID and charge description are required.");
              return;
            }
            try {
              const row = await api<any>("/api/citations", {
                method: "POST",
                body: JSON.stringify({
                  subject_id: Number(subject),
                  fine: Number(fine) || 0,
                  form: {
                    ...form,
                    court_date: `${form.court_month} ${form.court_day}, ${form.court_year}`,
                    describe_charge: form.charge_description,
                  },
                }),
              });
              setMsg(`Citation ${row.case_number} issued.`);
              setList(await api<{ results: any[] }>("/api/citations").then((r) => r.results));
            } catch (e: any) {
              setErr(e.message);
            }
          }}
        >
          Issue summons
        </button>
      </div>
      <Panel title="Recent citations">
        {(list || []).map((c) => (
          <div key={c.id} className="unit-row">
            <strong>{c.case_number}</strong>
            <span>{c.violation}</span>
            <StatusBadge value={c.payment_status} />
          </div>
        ))}
      </Panel>
    </div>
  );
}

export function WarrantIssueForm() {
  const [catalog, setCatalog] = useState<any>({ charges: [] });
  const [officer, setOfficer] = useState<any>({});
  const [subject, setSubject] = useState("");
  const [msg, setMsg] = useState("");
  const [err, setErr] = useState("");
  const [list, setList] = useState<any[]>([]);
  const n = nowParts();
  const [form, setForm] = useState<any>({
    warrant_type: "Arrest",
    charge_class: "Misdemeanor",
    jurisdiction: "County",
    county: "WYTHE",
    courts: { general_district: false, criminal: false, traffic: false, jdr: false },
    va_code: "",
    code_description: "",
    accused: "",
    sex: "M",
    race: "",
    eyes: "",
    hair: "",
    issued_datetime: n.stamp,
  });
  const set = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  useEffect(() => {
    api("/api/catalog").then(setCatalog);
    api("/api/officer/profile").then(setOfficer);
    api<{ results: any[] }>("/api/warrants").then((r) => setList(r.results || [])).catch(() => {});
  }, []);
  return (
    <div className="stack">
      <PageHeader
        kicker="Records"
        title="Warrant of Arrest / Search"
        actions={
          <button type="button" onClick={() => window.print()}>
            Print
          </button>
        }
      />
      <div className="legal-doc">
        <h2>
          WARRANT OF ({form.warrant_type}) — ({form.charge_class}) ({form.jurisdiction})
        </h2>
        <div className="form-grid">
          <Field label="Warrant type">
            <select value={form.warrant_type} onChange={(e) => set("warrant_type", e.target.value)}>
              <option>Arrest</option>
              <option>Search</option>
            </select>
          </Field>
          <Field label="Offense classification">
            <select value={form.charge_class} onChange={(e) => set("charge_class", e.target.value)}>
              <option>Felony</option>
              <option>Misdemeanor</option>
            </select>
          </Field>
          <Field label="Warrant jurisdiction">
            <select value={form.jurisdiction} onChange={(e) => set("jurisdiction", e.target.value)}>
              <option>State</option>
              <option>County</option>
            </select>
          </Field>
          <Field label="Subject Discord ID">
            <input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </Field>
        </div>
        <h3>Jurisdiction and court</h3>
        <Field label="County of">
          <input value={form.county} onChange={(e) => set("county", e.target.value)} />
        </Field>
        <div className="checks">
          {[
            ["general_district", "General District Court"],
            ["criminal", "Criminal"],
            ["traffic", "Traffic"],
            ["jdr", "Juvenile and Domestic Relations District Court"],
          ].map(([k, lab]) => (
            <label key={k}>
              <input
                type="checkbox"
                checked={Boolean(form.courts[k])}
                onChange={(e) => set("courts", { ...form.courts, [k]: e.target.checked })}
              />
              {lab}
            </label>
          ))}
        </div>
        <h3>Offense information</h3>
        <div className="form-grid">
          <Field label="Code of Virginia">
            <input
              list="va-codes-w"
              value={form.va_code}
              onChange={(e) => {
                const code = e.target.value;
                const hit = (catalog.charges || []).find((c: any) => c.code === code);
                setForm((f: any) => ({
                  ...f,
                  va_code: code,
                  code_description: hit?.description || f.code_description,
                }));
              }}
            />
            <datalist id="va-codes-w">
              {(catalog.charges || []).map((c: any) => (
                <option key={c.code} value={c.code} />
              ))}
            </datalist>
          </Field>
          <Field label="Code description" span>
            <textarea value={form.code_description} onChange={(e) => set("code_description", e.target.value)} />
          </Field>
        </div>
        <h3>Accused information</h3>
        <div className="form-grid">
          <Field label="Accused: Last, First, Middle Name" span>
            <input value={form.accused} onChange={(e) => set("accused", e.target.value)} />
          </Field>
          <Field label="Sex">
            <select value={form.sex} onChange={(e) => set("sex", e.target.value)}>
              <option>M</option>
              <option>F</option>
              <option>Unspecified</option>
            </select>
          </Field>
          <Field label="Race">
            <select value={form.race} onChange={(e) => set("race", e.target.value)}>
              <option value="">Select</option>
              <option>Black</option>
              <option>White</option>
              <option>Hispanic</option>
              <option>Asian</option>
              <option>Native American</option>
              <option>Other</option>
            </select>
          </Field>
          <Field label="Eyes">
            <input value={form.eyes} onChange={(e) => set("eyes", e.target.value)} />
          </Field>
          <Field label="Hair">
            <input value={form.hair} onChange={(e) => set("hair", e.target.value)} />
          </Field>
          <Field label="Date & time issued" auto>
            <input value={form.issued_datetime} onChange={(e) => set("issued_datetime", e.target.value)} />
          </Field>
          <Field label="Issuing officer" auto>
            <input value={officer.issued_by || ""} readOnly />
          </Field>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {msg && <p>{msg}</p>}
      <div className="toolbar">
        <button
          className="danger"
          type="button"
          onClick={async () => {
            setErr("");
            if (!subject || !form.accused) {
              setErr("Subject Discord ID and accused name are required.");
              return;
            }
            try {
              const row = await api<any>("/api/warrants", {
                method: "POST",
                body: JSON.stringify({ subject_id: Number(subject), form }),
              });
              setMsg(`Warrant ${row.case_number} created.`);
              setList(await api<{ results: any[] }>("/api/warrants").then((r) => r.results));
            } catch (e: any) {
              setErr(e.message);
            }
          }}
        >
          Issue warrant
        </button>
      </div>
      <Panel title="Warrant file">
        {(list || []).map((w) => (
          <div key={w.id} className="unit-row">
            <strong>{w.case_number}</strong>
            <span>{w.reason}</span>
            <StatusBadge value={w.status} />
          </div>
        ))}
      </Panel>
    </div>
  );
}

export function ServeWarrantForm() {
  const [list, setList] = useState<any[]>([]);
  const [selected, setSelected] = useState<any>(null);
  const [officer, setOfficer] = useState<any>({});
  const [err, setErr] = useState("");
  const [msg, setMsg] = useState("");
  const n = nowParts();
  const [serve, setServe] = useState({ arresting_officer: "", badge_agency_jurisdiction: "", served_datetime: n.stamp });
  useEffect(() => {
    api("/api/officer/profile").then((p: any) => {
      setOfficer(p);
      setServe((s) => ({
        ...s,
        arresting_officer: p.name || "",
        badge_agency_jurisdiction: [p.callsign, p.agency, "Wythe County"].filter(Boolean).join(", "),
      }));
    });
    api<{ results: any[] }>("/api/warrants").then((r) => setList(r.results || []));
  }, []);
  const form = selected?.form || {};
  const already = selected && String(selected.status).toLowerCase() === "served";
  return (
    <div className="stack">
      <PageHeader kicker="Records" title="Served warrant" actions={<button type="button" onClick={() => window.print()}>Print</button>} />
      <Panel title="Select warrant">
        <select
          value={selected?.id || ""}
          onChange={async (e) => {
            const id = Number(e.target.value);
            if (!id) {
              setSelected(null);
              return;
            }
            const row = await api<any>(`/api/warrants/${id}`);
            setSelected(row);
          }}
        >
          <option value="">Choose an active or historical warrant</option>
          {list.map((w) => (
            <option key={w.id} value={w.id}>
              {w.case_number} — {w.reason} ({w.status})
            </option>
          ))}
        </select>
      </Panel>
      {already && <p className="banner">This warrant is already marked served. Original issue data is preserved.</p>}
      <div className="legal-doc">
        <h2>SERVED WARRANT</h2>
        <h3>Accused information</h3>
        <div className="form-grid">
          <Field label="Accused" auto span>
            <input value={form.accused || ""} readOnly />
          </Field>
          <Field label="Case no." auto>
            <input value={selected?.case_number || ""} readOnly />
          </Field>
          <Field label="Date & time issued" auto>
            <input value={form.issued_datetime || selected?.created_at || ""} readOnly />
          </Field>
        </div>
        <h3>Service information</h3>
        <div className="form-grid">
          <Field label="Arresting officer" auto span>
            <input
              value={serve.arresting_officer}
              onChange={(e) => setServe({ ...serve, arresting_officer: e.target.value })}
            />
          </Field>
          <Field label="Badge no., agency, & jurisdiction" auto span>
            <input
              value={serve.badge_agency_jurisdiction}
              onChange={(e) => setServe({ ...serve, badge_agency_jurisdiction: e.target.value })}
            />
          </Field>
          <Field label="Date & time served" auto>
            <input value={serve.served_datetime} onChange={(e) => setServe({ ...serve, served_datetime: e.target.value })} />
          </Field>
        </div>
      </div>
      {err && <p className="err">{err}</p>}
      {msg && <p>{msg}</p>}
      <button
        className="ok"
        type="button"
        disabled={!selected || already}
        onClick={async () => {
          setErr("");
          try {
            const row = await api<any>(`/api/warrants/${selected.id}/serve`, {
              method: "POST",
              body: JSON.stringify({
                ...serve,
                accused: form.accused,
                original_issued: form.issued_datetime,
              }),
            });
            setMsg(`Warrant ${row.case_number} marked served.`);
            setList(await api<{ results: any[] }>("/api/warrants").then((r) => r.results));
            setSelected(await api(`/api/warrants/${selected.id}`));
          } catch (e: any) {
            setErr(e.message);
          }
        }}
      >
        Record service
      </button>
    </div>
  );
}
