import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";
import { Me, api } from "./api";

function has(me: Me | null, ...keys: string[]) {
  if (!me) return false;
  if (me.permissions.includes("cad.admin")) return true;
  return keys.some((k) => me.permissions.includes(k));
}

function takeSessionFromHash() {
  const hash = window.location.hash || "";
  const m = /(?:^[#&]|#)cad=([^&]+)/.exec(hash);
  if (!m) return false;
  try {
    sessionStorage.setItem("vsrp_cad", decodeURIComponent(m[1]));
    localStorage.removeItem("vsrp_cad");
  } catch {
    /* ignore */
  }
  window.history.replaceState(null, "", window.location.pathname + window.location.search);
  return true;
}

function startDiscordLogin() {
  try {
    sessionStorage.removeItem("vsrp_cad");
    localStorage.removeItem("vsrp_cad");
  } catch {
    /* ignore */
  }
  document.cookie = "vsrp_cad=; Path=/; Max-Age=0";
  window.location.href = "/api/auth/discord/login";
}

export default function App() {
  const [me, setMe] = useState<Me | null | undefined>(undefined);
  useEffect(() => {
    takeSessionFromHash();
    api<Me>("/api/auth/me")
      .then((user) => {
        if (!user?.discord_user_id) setMe(null);
        else setMe(user);
      })
      .catch(() => setMe(null));
  }, []);
  if (me === undefined) return <div className="main">Loading CAD…</div>;
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/verify" element={me ? <Verify me={me} /> : <Navigate to="/login" />} />
      <Route path="/*" element={me ? <Shell me={me} /> : <Navigate to="/login" />} />
    </Routes>
  );
}

function Login() {
  return (
    <div className="login">
      <div className="login-card">
        <p className="muted" style={{ letterSpacing: "0.14em", textTransform: "uppercase" }}>
          VSRP Operations
        </p>
        <h2 style={{ marginTop: 8 }}>Computer-Aided Dispatch</h2>
        <p className="muted">Sign in with the Discord account on the main roleplay server. CAD will not open until Discord authorizes you.</p>
        <button className="primary" type="button" onClick={startDiscordLogin}>
          Continue with Discord
        </button>
      </div>
    </div>
  );
}

function Verify({ me }: { me: Me }) {
  const nav = useNavigate();
  const [code, setCode] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="login">
      <div className="login-card">
        <h2>Discord DM verification</h2>
        <p className="muted">
          Elevated CAD consoles require a one-time code sent by the bot. This is not a replacement for Discord login.
        </p>
        <div className="row">
          <button
            onClick={async () => {
              try {
                await api("/api/auth/dm-code/request", { method: "POST" });
                setMsg("Code queued. Check DMs from the bot.");
              } catch (e: any) {
                setMsg(e.message);
              }
            }}
          >
            Send code
          </button>
        </div>
        <div className="row">
          <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="6-digit code" />
          <button
            className="primary"
            onClick={async () => {
              try {
                await api("/api/auth/dm-code/verify", { method: "POST", body: JSON.stringify({ code }) });
                nav("/");
                window.location.reload();
              } catch (e: any) {
                setMsg(e.message);
              }
            }}
          >
            Verify
          </button>
        </div>
        {msg && <p className="muted">{msg}</p>}
        <p className="muted">Signed in as {me.global_name || me.username}</p>
      </div>
    </div>
  );
}

function Shell({ me }: { me: Me }) {
  const links: [string, string, string[]][] = [
    ["/", "Dashboard", []],
    ["/civilian", "Civilian", ["civilian.self"]],
    ["/mdt", "Police MDT", ["mdt.police"]],
    ["/dispatch", "Dispatch", ["dispatch.operate"]],
    ["/fire", "Fire", ["mdt.fire"]],
    ["/ems", "EMS", ["mdt.ems"]],
    ["/dot", "DOT", ["mdt.dot"]],
    ["/map", "Map", ["mdt.police", "mdt.fire", "mdt.ems", "mdt.dot", "dispatch.operate"]],
    ["/warrants", "Warrants", ["warrants.manage", "records.read"]],
    ["/bolos", "BOLOs", ["bolos.manage", "records.read"]],
    ["/shifts", "Shifts", ["shifts.read"]],
    ["/applications", "Applications", ["applications.review"]],
    ["/admin", "Admin", ["cad.admin"]],
  ];
  return (
    <div className="layout">
      <nav className="rail">
        <h1>VSRP CAD</h1>
        {links
          .filter(([, , need]) => !need.length || has(me, ...need))
          .map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              {label}
            </NavLink>
          ))}
        <button
          style={{ marginTop: 16, width: "100%" }}
          onClick={async () => {
            await api("/api/auth/logout", { method: "POST" });
            try {
              localStorage.removeItem("vsrp_cad");
              sessionStorage.removeItem("vsrp_cad");
            } catch {
              /* ignore */
            }
            window.location.href = "/login";
          }}
        >
          Sign out
        </button>
        <p className="muted" style={{ marginTop: 12 }}>
          {me.global_name || me.username}
        </p>
      </nav>
      <div className="main">
        {me.needs_dm_verify && (
          <div className="banner">
            DM verification required for elevated consoles. <NavLink to="/verify">Verify now</NavLink>
          </div>
        )}
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/civilian" element={<Civilian />} />
          <Route path="/mdt" element={<Mdt />} />
          <Route path="/mdt/people/:id" element={<Person />} />
          <Route path="/dispatch" element={<Dispatch />} />
          <Route path="/fire" element={<DeptCalls path="/api/fire/calls" title="Fire" />} />
          <Route path="/ems" element={<Ems />} />
          <Route path="/dot" element={<Dot />} />
          <Route path="/map" element={<MapPage />} />
          <Route path="/warrants" element={<Warrants />} />
          <Route path="/bolos" element={<Bolos />} />
          <Route path="/shifts" element={<Shifts />} />
          <Route path="/applications" element={<Applications />} />
          <Route path="/admin" element={<Admin />} />
        </Routes>
      </div>
    </div>
  );
}

function Dashboard() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    api("/api/dashboard").then(setData).catch((e) => setErr(e.message));
  }, []);
  if (err) return <p className="err">{err}</p>;
  if (!data) return <p>Loading dashboard…</p>;
  return (
    <>
      <h2>Operations dashboard</h2>
      <div className="cards">
        <div className="card">
          <div className="muted">Open calls</div>
          <div className="n">{data.open_calls}</div>
        </div>
        <div className="card">
          <div className="muted">Pending dispatch</div>
          <div className="n">{data.pending_calls}</div>
        </div>
        <div className="card">
          <div className="muted">Active BOLOs</div>
          <div className="n">{data.active_bolos}</div>
        </div>
        <div className="card">
          <div className="muted">Your unpaid citations</div>
          <div className="n">{data.my_unpaid_citations}</div>
        </div>
      </div>
      <h3>Recent calls</h3>
      <Table
        rows={data.recent_calls}
        cols={[
          ["call_number", "Call"],
          ["discipline", "Disc."],
          ["priority", "Pri"],
          ["status", "Status"],
          ["nature", "Nature"],
          ["location_text", "Location"],
        ]}
        empty="No calls in the database."
      />
    </>
  );
}

function Table({ rows, cols, empty }: { rows: any[]; cols: [string, string][]; empty: string }) {
  if (!rows?.length) return <p className="muted">{empty}</p>;
  return (
    <table>
      <thead>
        <tr>
          {cols.map(([k, l]) => (
            <th key={k}>{l}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r.id ?? i}>
            {cols.map(([k]) => (
              <td key={k}>{String(r[k] ?? "")}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Civilian() {
  const [data, setData] = useState<any>(null);
  const [name, setName] = useState("");
  useEffect(() => {
    api("/api/civilian/me").then(setData).catch(() => setData({ error: true }));
  }, []);
  if (!data) return <p>Loading…</p>;
  if (data.error) return <p className="err">Could not load civilian file.</p>;
  const uid = data.user?.user_id;
  return (
    <>
      <h2>Civilian portal</h2>
      <p className="muted">Discord ID {uid}. Fictional character names are not proof of identity.</p>
      <h3>Characters</h3>
      <Table rows={data.characters} cols={[["display_name", "Name"], ["dob", "DOB"]]} empty="No characters yet." />
      <div className="row">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Character name" />
        <button
          className="primary"
          onClick={async () => {
            await api(`/api/people/${uid}/characters`, {
              method: "POST",
              body: JSON.stringify({ display_name: name, is_primary: true }),
            });
            setData(await api("/api/civilian/me"));
          }}
        >
          Add character
        </button>
      </div>
      <h3>Vehicles</h3>
      <Table rows={data.vehicles} cols={[["plate", "Plate"], ["make", "Make"], ["model", "Model"], ["status", "Status"]]} empty="No registered vehicles." />
      <h3>Citations</h3>
      <table>
        <thead>
          <tr>
            <th>Case</th>
            <th>Violation</th>
            <th>Fine</th>
            <th>Status</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {(data.citations || []).map((c: any) => (
            <tr key={c.id}>
              <td>{c.case_number}</td>
              <td>{c.violation}</td>
              <td>${c.fine}</td>
              <td>{c.payment_status}</td>
              <td>
                {c.payment_status === "unpaid" && (
                  <button
                    onClick={async () => {
                      await api(`/api/citations/${c.id}/pay`, { method: "POST" });
                      setData(await api("/api/civilian/me"));
                    }}
                  >
                    Pay
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <h3>Warrants</h3>
      <Table rows={data.warrants} cols={[["case_number", "Case"], ["reason", "Reason"], ["status", "Status"]]} empty="No warrants." />
    </>
  );
}

function Mdt() {
  const [q, setQ] = useState("");
  const [plate, setPlate] = useState("");
  const [people, setPeople] = useState<any[]>([]);
  const [vehicles, setVehicles] = useState<any[]>([]);
  const [subject, setSubject] = useState("");
  const [fine, setFine] = useState("50");
  const [charge, setCharge] = useState("");
  const [msg, setMsg] = useState("");
  const nav = useNavigate();
  return (
    <>
      <h2>Police MDT</h2>
      <div className="row">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Name or Discord ID" />
        <button
          onClick={async () => {
            const r = await api<{ results: any[] }>(`/api/people?q=${encodeURIComponent(q)}`);
            setPeople(r.results);
          }}
        >
          Search people
        </button>
        <input value={plate} onChange={(e) => setPlate(e.target.value)} placeholder="Plate" />
        <button
          onClick={async () => {
            const r = await api<{ results: any[] }>(`/api/vehicles?plate=${encodeURIComponent(plate)}`);
            setVehicles(r.results);
          }}
        >
          Search plate
        </button>
      </div>
      {people.map((p) => (
        <p key={p.user_id}>
          <button onClick={() => nav(`/mdt/people/${p.user_id}`)}>
            {p.display_name || p.user_id}
          </button>
        </p>
      ))}
      <Table rows={vehicles} cols={[["plate", "Plate"], ["make", "Make"], ["model", "Model"], ["user_id", "Owner"]]} empty={plate ? "No vehicles." : "Search a plate."} />
      <h3>Issue citation</h3>
      <div className="row">
        <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject Discord ID" />
        <input value={fine} onChange={(e) => setFine(e.target.value)} placeholder="Fine" />
        <input value={charge} onChange={(e) => setCharge(e.target.value)} placeholder="Charge description" />
        <button
          className="primary"
          onClick={async () => {
            try {
              const row = await api<any>("/api/citations", {
                method: "POST",
                body: JSON.stringify({
                  subject_id: Number(subject),
                  fine: Number(fine),
                  form: { charge_description: charge, va_code: "", court_type: "GDC" },
                }),
              });
              setMsg(`Citation ${row.case_number} issued. Discord delivery queued.`);
            } catch (e: any) {
              setMsg(e.message);
            }
          }}
        >
          Issue
        </button>
      </div>
      <h3>Issue warrant</h3>
      <WarrantForm />
      {msg && <p>{msg}</p>}
    </>
  );
}

function WarrantForm() {
  const [subject, setSubject] = useState("");
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="row">
      <input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject Discord ID" />
      <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Charge / reason" />
      <button
        className="danger"
        onClick={async () => {
          try {
            const row = await api<any>("/api/warrants", {
              method: "POST",
              body: JSON.stringify({
                subject_id: Number(subject),
                form: { code_description: reason, warrant_type: "arrest" },
              }),
            });
            setMsg(`Warrant ${row.case_number} created.`);
          } catch (e: any) {
            setMsg(e.message);
          }
        }}
      >
        Issue warrant
      </button>
      {msg && <span>{msg}</span>}
    </div>
  );
}

function Person() {
  const { id } = useParams();
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    api(`/api/people/${id}`).then(setData).catch(() => setData({ error: true }));
  }, [id]);
  if (!data) return <p>Loading…</p>;
  if (data.error) return <p className="err">Not found or no access.</p>;
  return (
    <>
      <h2>Person {id}</h2>
      <p className="muted">
        License suspended: {data.user.license_suspended ? "yes" : "no"} · Registration suspended:{" "}
        {data.user.registration_suspended ? "yes" : "no"}
      </p>
      <h3>Vehicles</h3>
      <Table rows={data.vehicles} cols={[["plate", "Plate"], ["make", "Make"], ["model", "Model"], ["status", "Status"]]} empty="None" />
      <h3>Citations</h3>
      <Table rows={data.citations} cols={[["case_number", "Case"], ["violation", "Violation"], ["payment_status", "Pay"]]} empty="None" />
      <h3>Warrants</h3>
      <Table rows={data.warrants} cols={[["case_number", "Case"], ["reason", "Reason"], ["status", "Status"]]} empty="None" />
    </>
  );
}

function Dispatch() {
  const [calls, setCalls] = useState<any[]>([]);
  const [units, setUnits] = useState<any[]>([]);
  const [nature, setNature] = useState("");
  const [loc, setLoc] = useState("");
  const [disc, setDisc] = useState("police");
  const [callsign, setCallsign] = useState("");
  const [dept, setDept] = useState("police2");
  const refresh = async () => {
    const c = await api<{ results: any[] }>("/api/calls");
    const u = await api<{ results: any[] }>("/api/units");
    setCalls(c.results);
    setUnits(u.results);
  };
  useEffect(() => {
    refresh();
    const ws = new WebSocket(`${location.origin.replace("http", "ws")}/api/ws`);
    ws.onmessage = () => refresh();
    return () => ws.close();
  }, []);
  return (
    <>
      <h2>Dispatch</h2>
      <div className="row">
        <select value={disc} onChange={(e) => setDisc(e.target.value)}>
          <option>police</option>
          <option>fire</option>
          <option>ems</option>
          <option>dot</option>
          <option>multi</option>
        </select>
        <input value={nature} onChange={(e) => setNature(e.target.value)} placeholder="Nature" />
        <input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Location" />
        <button
          className="primary"
          onClick={async () => {
            await api("/api/calls", {
              method: "POST",
              body: JSON.stringify({ discipline: disc, nature, location_text: loc, priority: 3 }),
            });
            refresh();
          }}
        >
          Create call
        </button>
      </div>
      <h3>Active / recent calls</h3>
      {!calls.length && <p className="muted">No calls.</p>}
      {calls.map((c) => (
        <div key={c.id} className="card" style={{ marginBottom: 8 }}>
          <strong>{c.call_number}</strong> [{c.discipline}] pri {c.priority} — {c.status}
          <div className="muted">{c.nature} @ {c.location_text}</div>
          <div className="row">
            {units.map((u) => (
              <button
                key={u.id}
                onClick={async () => {
                  await api(`/api/calls/${c.id}/assign`, { method: "POST", body: JSON.stringify({ unit_id: u.id }) });
                  refresh();
                }}
              >
                Assign {u.callsign}
              </button>
            ))}
            <button
              onClick={async () => {
                await api(`/api/calls/${c.id}/close`, { method: "POST" });
                refresh();
              }}
            >
              Close
            </button>
          </div>
        </div>
      ))}
      <h3>Units</h3>
      <div className="row">
        <input value={callsign} onChange={(e) => setCallsign(e.target.value)} placeholder="Callsign" />
        <select value={dept} onChange={(e) => setDept(e.target.value)}>
          <option>police1</option>
          <option>police2</option>
          <option>fire1</option>
          <option>fire2</option>
          <option>fire3</option>
          <option>ems</option>
          <option>dot</option>
          <option>dispatch</option>
        </select>
        <button
          onClick={async () => {
            await api("/api/units", { method: "POST", body: JSON.stringify({ callsign, department: dept }) });
            refresh();
          }}
        >
          Add unit
        </button>
      </div>
      {units.map((u) => (
        <div key={u.id} className="row">
          <span>
            {u.callsign} {u.department} <span className={`status-${u.status}`}>{u.status}</span>
          </span>
          {["available", "en_route", "on_scene", "busy", "out_of_service"].map((s) => (
            <button
              key={s}
              onClick={async () => {
                await api(`/api/units/${u.id}/status`, { method: "POST", body: JSON.stringify({ status: s }) });
                refresh();
              }}
            >
              {s}
            </button>
          ))}
        </div>
      ))}
      <h3>Pager</h3>
      <Pager />
    </>
  );
}

function Pager() {
  const [department, setDepartment] = useState("fire1");
  const [message, setMessage] = useState("");
  const [msg, setMsg] = useState("");
  return (
    <div className="row">
      <select value={department} onChange={(e) => setDepartment(e.target.value)}>
        <option value="fire1">WFR</option>
        <option value="fire2">RRVFD</option>
        <option value="fire3">MMVFD</option>
        <option value="police1">WCSO</option>
        <option value="police2">WPD</option>
        <option value="ems">EMS</option>
        <option value="dot">DOT</option>
        <option value="dispatch">Dispatch</option>
      </select>
      <input value={message} onChange={(e) => setMessage(e.target.value)} placeholder="Alert message" />
      <button
        className="danger"
        onClick={async () => {
          await api("/api/dispatch/page", { method: "POST", body: JSON.stringify({ department, message }) });
          setMsg("Queued for Discord.");
        }}
      >
        Page
      </button>
      {msg && <span className="muted">{msg}</span>}
    </div>
  );
}

function DeptCalls({ path, title }: { path: string; title: string }) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    api<{ results: any[] }>(path).then((r) => setRows(r.results));
  }, [path]);
  return (
    <>
      <h2>{title}</h2>
      <Table rows={rows} cols={[["call_number", "Call"], ["status", "Status"], ["nature", "Nature"], ["location_text", "Location"]]} empty="No incidents." />
    </>
  );
}

function Ems() {
  const [data, setData] = useState<any>({ results: [], pcrs: [] });
  const [patient, setPatient] = useState("");
  const [cc, setCc] = useState("");
  useEffect(() => {
    api("/api/ems/calls").then(setData);
  }, []);
  return (
    <>
      <h2>EMS</h2>
      <Table rows={data.results} cols={[["call_number", "Call"], ["status", "Status"], ["nature", "Nature"]]} empty="No EMS calls." />
      <h3>Patient care report (fictional)</h3>
      <div className="row">
        <input value={patient} onChange={(e) => setPatient(e.target.value)} placeholder="Patient name" />
        <input value={cc} onChange={(e) => setCc(e.target.value)} placeholder="Chief complaint" />
        <button
          onClick={async () => {
            await api("/api/ems/pcr", { method: "POST", body: JSON.stringify({ patient_name: patient, chief_complaint: cc }) });
            setData(await api("/api/ems/calls"));
          }}
        >
          File PCR
        </button>
      </div>
      <Table rows={data.pcrs} cols={[["patient_name", "Patient"], ["chief_complaint", "Complaint"], ["disposition", "Disp."]]} empty="No PCRs." />
    </>
  );
}

function Dot() {
  const [data, setData] = useState<any>({ results: [], closures: [] });
  const [loc, setLoc] = useState("");
  useEffect(() => {
    api("/api/dot/calls").then(setData);
  }, []);
  return (
    <>
      <h2>DOT operations</h2>
      <Table rows={data.results} cols={[["call_number", "Call"], ["status", "Status"], ["nature", "Nature"]]} empty="No DOT calls." />
      <h3>Road closures</h3>
      <div className="row">
        <input value={loc} onChange={(e) => setLoc(e.target.value)} placeholder="Location" />
        <button
          className="primary"
          onClick={async () => {
            await api("/api/dot/closures", { method: "POST", body: JSON.stringify({ location_text: loc }) });
            setData(await api("/api/dot/calls"));
          }}
        >
          Open closure
        </button>
      </div>
      {(data.closures || []).map((c: any) => (
        <div key={c.id} className="row">
          <span>
            {c.location_text} — {c.status}
          </span>
          {c.status === "open" && (
            <button
              onClick={async () => {
                await api(`/api/dot/closures/${c.id}/clear`, { method: "POST" });
                setData(await api("/api/dot/calls"));
              }}
            >
              Clear
            </button>
          )}
        </div>
      ))}
    </>
  );
}

function MapPage() {
  const [data, setData] = useState<any>({ units: [], unplaced: [], calls: [] });
  useEffect(() => {
    api("/api/map/units").then((d) => {
      setData(d);
      const L = window.L;
      if (!L) return;
      const el = document.getElementById("map");
      if (!el || (el as any)._leaflet) return;
      const map = L.map("map").setView([36.95, -81.08], 11);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
      }).addTo(map);
      (d as any).units.forEach((u: any) => {
        L.marker([u.lat, u.lng]).addTo(map).bindPopup(`${u.callsign} (${u.status})`);
      });
      (d as any).calls.forEach((c: any) => {
        L.circleMarker([c.lat, c.lng], { color: "#e74c3c" }).addTo(map).bindPopup(c.call_number);
      });
    });
  }, []);
  return (
    <>
      <h2>Map</h2>
      <p className="muted">Positions are manual. Discord does not provide GPS. Unplaced units stay off the map.</p>
      <div id="map" />
      <h3>Unplaced units</h3>
      <Table rows={data.unplaced} cols={[["callsign", "Unit"], ["department", "Dept"], ["status", "Status"]]} empty="All units placed or none exist." />
    </>
  );
}

function Warrants() {
  const [rows, setRows] = useState<any[]>([]);
  const load = () => api<{ results: any[] }>("/api/warrants").then((r) => setRows(r.results));
  useEffect(() => {
    load();
  }, []);
  return (
    <>
      <h2>Warrants</h2>
      <WarrantForm />
      {rows.map((w) => (
        <div key={w.id} className="row">
          <span>
            #{w.case_number} {w.reason} — {w.status}
          </span>
          {w.active === 1 && (
            <>
              <button
                className="ok"
                onClick={async () => {
                  await api(`/api/warrants/${w.id}/serve`, { method: "POST", body: "{}" });
                  load();
                }}
              >
                Serve
              </button>
              <button
                onClick={async () => {
                  await api(`/api/warrants/${w.id}/clear`, { method: "POST" });
                  load();
                }}
              >
                Clear
              </button>
            </>
          )}
        </div>
      ))}
      {!rows.length && <p className="muted">No warrants.</p>}
    </>
  );
}

function Bolos() {
  const [rows, setRows] = useState<any[]>([]);
  const [desc, setDesc] = useState("");
  const [kind, setKind] = useState("person");
  const load = () => api<{ results: any[] }>("/api/bolos").then((r) => setRows(r.results));
  useEffect(() => {
    load();
  }, []);
  return (
    <>
      <h2>BOLOs</h2>
      <div className="row">
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="person">person</option>
          <option value="vehicle">vehicle</option>
        </select>
        <input value={desc} onChange={(e) => setDesc(e.target.value)} placeholder="Description" />
        <button
          className="primary"
          onClick={async () => {
            await api("/api/bolos", { method: "POST", body: JSON.stringify({ kind, description: desc }) });
            load();
          }}
        >
          Create
        </button>
      </div>
      {rows.map((b) => (
        <div key={b.id} className="row">
          <span>
            {b.kind} {b.status}: {b.description}
          </span>
          {b.status === "active" && (
            <button
              onClick={async () => {
                await api(`/api/bolos/${b.id}/clear`, { method: "POST" });
                load();
              }}
            >
              Clear
            </button>
          )}
        </div>
      ))}
    </>
  );
}

function Shifts() {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    api<{ results: any[] }>("/api/shifts/active").then((r) => setRows(r.results));
  }, []);
  return (
    <>
      <h2>Active shifts</h2>
      <p className="muted">Start/stop remains on FD/PD Discord (`/shift`).</p>
      <Table rows={rows} cols={[["user_id", "User"], ["guild_id", "Guild"], ["status", "Status"], ["started_at", "Started"]]} empty="No active shifts." />
    </>
  );
}

function Applications() {
  const [rows, setRows] = useState<any[]>([]);
  const [detail, setDetail] = useState<any>(null);
  useEffect(() => {
    api<{ results: any[] }>("/api/applications").then((r) => setRows(r.results));
  }, []);
  return (
    <>
      <h2>Applications</h2>
      <p className="muted">Approvals stay in Discord so role grants are not duplicated.</p>
      {rows.map((a) => (
        <button key={a.id} onClick={async () => setDetail(await api(`/api/applications/${a.id}`))}>
          #{a.id} {a.department} {a.status}
        </button>
      ))}
      {detail && (
        <div className="card" style={{ marginTop: 12 }}>
          {(detail.answers || []).map((x: any, i: number) => (
            <p key={i}>
              <strong>{x.question}</strong>
              <br />
              {x.answer}
            </p>
          ))}
        </div>
      )}
    </>
  );
}

function Admin() {
  const [audit, setAudit] = useState<any>({ cad: [], bot: [] });
  useEffect(() => {
    api("/api/admin/audit").then(setAudit);
  }, []);
  return (
    <>
      <h2>Administration</h2>
      <h3>CAD audit</h3>
      <Table rows={audit.cad} cols={[["created_at", "When"], ["actor_id", "Actor"], ["action", "Action"], ["entity_type", "Entity"]]} empty="No CAD audit events." />
    </>
  );
}
