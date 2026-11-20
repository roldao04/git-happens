// HTTPS for the phone, trusted instead of self-signed: a certificate authority made on this laptop (once),
// and a certificate for this laptop's addresses signed by it (remade when the Wi-Fi address changes).
// The phone trusts it after installing the authority's certificate once (the helper on port 5175 serves it).
//
//   npm run certs
//
// Everything lives in .certs/ (gitignored). The authority's key never leaves this laptop.
import { X509Certificate } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { createCA, createCert } from "mkcert";

export const APP_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
export const CERT_DIR = path.join(APP_DIR, ".certs");
export const FILES = {
  caCert: path.join(CERT_DIR, "alfred-dev-ca.crt"),
  caKey: path.join(CERT_DIR, "alfred-dev-ca.key"),
  cert: path.join(CERT_DIR, "dev.crt"),
  key: path.join(CERT_DIR, "dev.key"),
  hosts: path.join(CERT_DIR, "hosts.json"),
};

const CA_DAYS = 3650;
const CERT_DAYS = 365; // iPhones refuse server certificates valid for more than 825 days
const RENEW_DAYS = 30;

/** This laptop's IPv4 addresses on its networks (Wi-Fi, Ethernet), not loopback. */
export function lanAddresses() {
  return Object.values(os.networkInterfaces())
    .flat()
    .filter((a) => a && a.family === "IPv4" && !a.internal)
    .map((a) => a.address)
    .sort();
}

export function hostsNow() {
  return ["localhost", "127.0.0.1", `${os.hostname()}.local`, ...lanAddresses()];
}

function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch {
    return null;
  }
}

function daysLeft(pem) {
  return (new Date(new X509Certificate(pem).validTo).getTime() - Date.now()) / 86_400_000;
}

/** Makes what's missing, keeps what's still good. Returns the file paths and the addresses covered. */
export async function ensureCerts({ log = console.log } = {}) {
  fs.mkdirSync(CERT_DIR, { recursive: true, mode: 0o700 });
  let ca;
  if (fs.existsSync(FILES.caCert) && fs.existsSync(FILES.caKey)) {
    ca = { cert: fs.readFileSync(FILES.caCert, "utf8"), key: fs.readFileSync(FILES.caKey, "utf8") };
  } else {
    ca = await createCA({ organization: `Alfred dev (${os.hostname()})`, countryCode: "PT", state: "Aveiro", locality: "Aveiro", validity: CA_DAYS });
    fs.writeFileSync(FILES.caCert, ca.cert);
    fs.writeFileSync(FILES.caKey, ca.key, { mode: 0o600 });
    for (const f of [FILES.cert, FILES.key, FILES.hosts]) fs.rmSync(f, { force: true }); // signed by the old one
    log("certs: made a new certificate authority for this laptop (install it on the phone once)");
  }
  const hosts = hostsNow();
  const covered = readJson(FILES.hosts);
  const fresh = fs.existsSync(FILES.cert) && fs.existsSync(FILES.key)
    && JSON.stringify(covered) === JSON.stringify(hosts)
    && daysLeft(fs.readFileSync(FILES.cert, "utf8")) > RENEW_DAYS;
  if (!fresh) {
    const leaf = await createCert({ ca, domains: hosts, validity: CERT_DAYS, organization: "Alfred dev" });
    fs.writeFileSync(FILES.cert, leaf.cert);
    fs.writeFileSync(FILES.key, leaf.key, { mode: 0o600 });
    fs.writeFileSync(FILES.hosts, JSON.stringify(hosts));
    log(`certs: certificate for ${hosts.join(", ")}`);
  }
  return { ...FILES, hosts };
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const r = await ensureCerts();
  console.log(`certs: ready in ${path.relative(process.cwd(), CERT_DIR) || "."} for ${r.hosts.join(", ")}`);
}
