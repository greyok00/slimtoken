#!/usr/bin/env python3

import json, re, sqlite3, sys
from pathlib import Path

DB = Path.home() / ".config/cortexllm" / "cortexllm.db"


CATEGORY_KEYWORDS = {
    "Network Security": ["nmap", "port scan", "network", "firewall", "dns", "subdomain",
                         "sniff", "packet", "cidr", "asn", "tcp", "udp"],
    "Web Security": ["xss", "csrf", "sqli", "sql injection", "web", "http", "https",
                     "cookie", "session", "cors", "waf", "csrf token", "x-frame"],
    "Authentication": ["password", "login", "auth", "mfa", "2fa", "oauth", "jwt",
                       "credential", "session", "token", "sso", "ldap"],
    "Input Validation": ["injection", "sanitize", "validate", "escape", "filter",
                         "parameter", "input", "boundary"],
    "Cryptography": ["encrypt", "decrypt", "hash", "cipher", "ssl", "tls", "certificate",
                     "key", "openssl", "heartbleed"],
    "Social Engineering": ["phishing", "vishing", "smishing", "pretext", "bait",
                           "tailgate", "social eng", "manipulation", "urgency",
                           "authority", "scam", "deepfake", "quishing"],
    "Penetration Testing": ["exploit", "metasploit", "msfvenom", "payload", "reverse shell",
                            "privesc", "privilege escalation", "post-exploit",
                            "latera move", "pivot", "c2", "command & control"],
    "Vulnerability Assessment": ["vulnerability", "cve", "scan", "nuclei", "nikto",
                                 "wpscan", "nessus", "openvas"],
    "OSINT": ["recon", "osint", "theharvester", "amass", "subfinder", "dnsrecon",
              "google dork", "whois", "shodan", "censys", "gau", "katana"],
    "Password Cracking": ["hashcat", "hydra", "john", "crack", "brute force",
                          "dictionary attack", "mask attack", "rockyou"],
    "Incident Response": ["incident", "response", "contain", "eradicate", "recover",
                          "triage", "soar", "playbook"],
    "Forensics": ["forensic", "artifact", "memory dump", "disk image", "volatility",
                  "autopsy", "sleuth"],
    "Cloud Security": ["cloud", "aws", "azure", "gcp", "k8s", "kubernetes",
                        "container", "docker", "s3", "iam"],
    "Mobile Security": ["mobile", "android", "ios", "mdm", "app sec"],
    "Compliance": ["compliance", "gdpr", "hipaa", "pci", "sox", "audit", "policy"],
    "Secure Coding": ["secure code", "code review", "static analysis", "lint",
                       "owasp", "cwe", "sdlc"],
    "Malware Analysis": ["malware", "ransomware", "trojan", "virus", "worm",
                         "analysis", "sandbox", "reverse engineer"],
    "Post-Exploitation": ["post-exploit", "lateral", "persistence", "dump", "hash",
                          "kerberoast", "bloodhound", "mimikatz"],
}

def guess_category(text: str) -> str:

    text_lower = text.lower()
    scores = {}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > 0:
            scores[cat] = score
    if scores:
        return max(scores, key=scores.get)
    return "Penetration Testing"


def insert_practice(category, practice, description, source, priority="medium", tags=None):
    conn = sqlite3.connect(str(DB))
    row = conn.execute(
        "SELECT id FROM Coding_Practices WHERE category=? AND practice=?",
        (category, practice)
    ).fetchone()
    if row:

        existing = conn.execute("SELECT description FROM Coding_Practices WHERE id=?", (row[0],)).fetchone()
        if existing and len(description) > len(existing[0]):
            conn.execute("UPDATE Coding_Practices SET description=?, last_updated=datetime('now') WHERE id=?", (description[:5000], row[0]))
            conn.commit()
            conn.close()
            return True
        conn.close()
        return False
    conn.execute(
        "INSERT INTO Coding_Practices (category, practice, description, source, priority, tags) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (category, practice[:120], description[:5000], source, priority, json.dumps(tags or []))
    )
    conn.commit()
    conn.close()
    return True




def parse_markdown(text: str, source: str) -> int:

    total = 0
    current_category = "Penetration Testing"
    lines = text.split("\n")


    CAT_HEADING_MAP = {
        "reconnaissance": "OSINT", "osint": "OSINT",
        "scanning": "Vulnerability Assessment", "enumeration": "Vulnerability Assessment",
        "vulnerability assessment": "Vulnerability Assessment", "exploitation": "Penetration Testing",
        "post exploitation": "Post-Exploitation", "password cracking": "Password Cracking",
        "social engineering": "Social Engineering",
        "web": "Web Security", "network": "Network Security",
        "mobile": "Mobile Security", "cloud": "Cloud Security",
        "forensics": "Forensics", "incident response": "Incident Response",
        "cryptography": "Cryptography", "authentication": "Authentication",
    }


    sections = []
    current_heading = "General"
    current_lines = []
    for line in lines:
        if line.startswith("## ") and not line.startswith("### "):
            if current_lines:
                sections.append((current_heading, current_lines))
            current_heading = line[3:].strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append((current_heading, current_lines))

    for heading, section_lines in sections:
        heading_lower = heading.lower()

        for key, cat in CAT_HEADING_MAP.items():
            if key in heading_lower:
                current_category = cat
                break


        section_text = "\n".join(section_lines)



        for j, line in enumerate(section_lines):
            s = line.strip()

            if s.startswith("### "):
                tool_name = s[4:].strip()
                if not tool_name or len(tool_name) < 2:
                    continue

                code_lines = []
                bullet_lines = []
                for k in range(j + 1, min(j + 20, len(section_lines))):
                    next_line = section_lines[k].strip()
                    if next_line.startswith("###") or next_line.startswith("##"):
                        break
                    if next_line.startswith("```"):
                        k += 1
                        while k < len(section_lines) and not section_lines[k].strip().startswith("```"):
                            cmd = section_lines[k].strip()
                            if cmd and not cmd.startswith("#"):
                                code_lines.append(cmd)
                            k += 1
                        break
                    if next_line.startswith("- ") or next_line.startswith("* "):
                        bullet_lines.append(next_line[2:].strip())

                desc_parts = []
                if code_lines:
                    desc_parts.append("Command:\n" + "\n".join(code_lines[:5]))
                if bullet_lines:
                    desc_parts.append("Options:\n" + "\n".join(bullet_lines[:5]))
                full_desc = "\n\n".join(desc_parts) if desc_parts else tool_name
                cat = guess_category(tool_name)
                prio = "high" if any(w in tool_name.lower() for w in ["critical", "essential"]) else "medium"
                if insert_practice(cat, tool_name, full_desc[:5000], source, prio, [cat.lower().replace(" ", "_")]):
                    total += 1
                    print(f"  ✅ [{cat}] {tool_name[:50]}")


        tools = re.findall(r'\*\*([^*]+)\*\*\s*[—–:-]?\s*([^\n]*)', section_text)
        for tool_name, tool_desc in tools:
            tool_name = tool_name.strip()
            tool_desc = tool_desc.strip()[:200]
            if len(tool_name) < 2 or len(tool_desc) < 5:
                continue
            if tool_name.lower() in ["note", "important", "tip", "warning", "see also", "key"]:
                continue
            full_desc = tool_desc
            cat = guess_category(tool_name + " " + tool_desc)
            prio = "high" if any(w in (tool_name + " " + tool_desc).lower() for w in ["critical", "always", "must", "essential"]) else "medium"
            if insert_practice(cat, tool_name, full_desc[:5000], source, prio, [cat.lower().replace(" ", "_")]):
                total += 1
                print(f"  ✅ [{cat}] {tool_name[:50]}")


        for line in section_lines:
            s = line.strip()
            if s.startswith("- ") or s.startswith("* "):
                content = s[2:].strip()
                if not content or len(content) < 15:
                    continue
                if content.startswith("[") or content.startswith("http"):
                    continue
                name = content.split(":")[0].split("—")[0].split("–")[0].strip()[:80]
                if len(name) < 4:
                    continue
                skip_words = ["key principles", "note:", "important:", "see also", "additional", "for example"]
                if any(name.lower().startswith(w) for w in skip_words):
                    continue
                cat = guess_category(content)
                prio = "high" if any(w in content.lower() for w in ["critical", "always", "must", "essential"]) else "medium"
                if insert_practice(cat, name, content[:500], source, prio, [cat.lower().replace(" ", "_")]):
                    total += 1
                    print(f"  ✅ [{cat}] {name[:50]}")

    return total




def parse_html(text: str, source: str) -> int:

    total = 0


    table_rows = re.findall(r'\|([^|]+)\|([^|]+)\|([^|]+)\|', text)
    for row in table_rows:
        cells = [c.strip() for c in row]
        if len(cells) >= 2:
            name = cells[0][:60]
            desc = cells[1][:150]
            if len(name) < 3 or name.startswith("---") or "Type" in name or "Description" in name:
                continue
            cat = guess_category(name + " " + desc)
            prio = "high" if any(w in (name + desc).lower() for w in ["critical", "always", "must", "essential"]) else "medium"
            if insert_practice(cat, name, desc, source, prio, [cat.lower().replace(" ", "_")]):
                total += 1
                print(f"  ✅ [{cat}] {name[:50]}")


    defs = re.findall(r'\*\*([^*]+)\*\*:\s*([^*]+)', text)
    for name, desc in defs:
        name = name.strip()[:60]
        desc = desc.strip()[:150]
        if len(name) < 3:
            continue
        cat = guess_category(name + " " + desc)
        if insert_practice(cat, name, desc, source, "medium", [cat.lower().replace(" ", "_")]):
            total += 1
            print(f"  ✅ [{cat}] {name[:50]}")

    return total




def parse_pdf_text(text: str, source: str) -> int:

    total = 0
    lines = text.split("\n")
    current_category = "Penetration Testing"

    for i, line in enumerate(lines):
        s = line.strip()
        if not s or len(s) < 15:
            continue


        if any(w in s.lower() for w in ["copyright", "all rights reserved", "ebscohost",
                                          "packt publishing", "www.", "http://", "https://",
                                          "printed on", "terms-of-use"]):
            continue


        if len(s) < 60 and s.isupper() and not s.startswith(" "):
            heading = s.lower()
            for key, cat in [("chapter", "Penetration Testing"), ("introduction", "Penetration Testing"),
                             ("network", "Network Security"), ("web", "Web Security"),
                             ("authentication", "Authentication"), ("cryptography", "Cryptography"),
                             ("malware", "Malware Analysis"), ("ransomware", "Malware Analysis"),
                             ("social", "Social Engineering"), ("mobile", "Mobile Security"),
                             ("cloud", "Cloud Security"), ("incident", "Incident Response"),
                             ("forensic", "Forensics"), ("vulnerability", "Vulnerability Assessment"),
                             ("exploit", "Penetration Testing"), ("password", "Password Cracking"),
                             ("recon", "OSINT"), ("osint", "OSINT")]:
                if key in heading:
                    current_category = cat
                    break
            continue


        if re.match(r'^\d+[.)]\s', s):
            name = re.sub(r'^\d+[.)]\s+', '', s)[:60]
            desc = s[:150]
            if len(name) < 5:
                continue
            cat = guess_category(s)
            if insert_practice(cat, name, desc, source, "medium", [cat.lower().replace(" ", "_")]):
                total += 1
                print(f"  ✅ [{cat}] {name[:50]}")


        if s.startswith("•") or s.startswith("-") or s.startswith("*"):
            content = s[1:].strip()
            if len(content) < 15:
                continue
            name = content.split(":")[0].split("—")[0][:60]
            desc = content[:150]
            cat = guess_category(content)
            if insert_practice(cat, name, desc, source, "medium", [cat.lower().replace(" ", "_")]):
                total += 1
                print(f"  ✅ [{cat}] {name[:50]}")

    return total




def list_sources():
    conn = sqlite3.connect(str(DB))
    rows = conn.execute("SELECT source, COUNT(*) as c FROM Coding_Practices GROUP BY source ORDER BY c DESC").fetchall()
    conn.close()
    print(f"{'Source':<55} {'Count':>6}")
    print(f"{'─'*55} {'─'*6}")
    for s, c in rows:
        print(f"{s[:54]:<55} {c:>6}")
    total = sum(r[1] for r in rows)
    print(f"{'─'*55} {'─'*6}")
    print(f"{'TOTAL':<55} {total:>6}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    if sys.argv[1] == "--list":
        list_sources()
        return 0

    if len(sys.argv) < 4:
        print("Usage: fast_extract.py --md/--html/--pdftext <file> --source <name>")
        return 1

    mode = sys.argv[1]
    filepath = sys.argv[2]
    source = sys.argv[4] if len(sys.argv) > 3 and sys.argv[3] == "--source" else "Unknown"

    if not Path(filepath).exists():
        print(f"File not found: {filepath}")
        return 1

    text = Path(filepath).read_text(encoding="utf-8", errors="replace")
    print(f"Processing {Path(filepath).name} ({len(text)} chars) for source '{source}'")

    if mode == "--md":
        total = parse_markdown(text, source)
    elif mode == "--html":
        total = parse_html(text, source)
    elif mode == "--pdftext":
        total = parse_pdf_text(text, source)
    else:
        print(f"Unknown mode: {mode}")
        return 1

    print(f"\n✅ Inserted {total} new practices from '{source}'")
    return 0


if __name__ == "__main__":
    main()
