# Test Cases: Decision Maker Selection

## The Problem

The system currently defaults to the **Geschäftsführer (CEO) from the Impressum** — even when the company has a team/about page with department heads who are a much better fit for the job category.

**Goal:** When a team page exists, prioritize the **most relevant decision maker for the job category** over the generic Impressum CEO.

---

## Case 1: moresophy GmbH — Data Engineer (IT)

The system picked **Prof. Dr. Heiko Beier (Geschäftsführer)** from the Impressum.
But on [moresophy.com/ueber-moresophy](https://www.moresophy.com/ueber-moresophy) there is **Dr. Christoph Schmidt (CTO)** — a much better match for a Data Engineer role.

**Test input:**
```json
{
  "category": "IT",
  "company": "moresophy GmbH",
  "contact_person": null,
  "date_posted": "2026-03-12T00:02:41+01:00",
  "description": "The MORESOPHY technology offers unique AI-supported methods for preparing high-quality, meaningful data for integration into cloud-based data management architectures. * Confident in office communication with digital technologies With us, you can expect an agile and structured way of working with daylies and sprints – an infrastructure with state-of-the-art cloud technologies, artificial intelligence and large, diverse amounts of data as well as an international, highly motivated team.",
  "id": "https://www.stepstone.de/stellenangebote--Data-Engineer-m-f-d-Munich-Germany-moresophy-GmbH--12331278-inline.html",
  "location": "Munich, Germany",
  "source": "Stepstone",
  "title": "Data Engineer (m/f/d)",
  "url": "https://www.stepstone.de/stellenangebote--Data-Engineer-m-f-d-Munich-Germany-moresophy-GmbH--12331278-inline.html",
  "sent_by_user": "Alexander Simon"
}
```

**Wrong output:** Prof. Dr. Heiko Beier, Geschäftsführer (from Impressum, only 2 candidates found)
**Expected output:** Dr. Christoph Schmidt, CTO (from team page)

---

## Case 2: BAGHUS GmbH — Microsoft Admin/Consultant (IT)

The system picked **André Bittruf (Geschäftsführer)** from the Impressum.
But on [baghus.de/ueber-uns](https://www.baghus.de/ueber-uns/) there are better matches:
- **Thomas** — Teamleiter Windows & Infrastruktur
- **Veronica** — Teamleiterin IT-Service-Management & ISB

**Test input:**
```json
{
  "category": "IT",
  "company": "BAGHUS GmbH",
  "contact_person": null,
  "date_posted": "2026-03-12",
  "description": "Komm ins beste Team!\n\nIT ist Deine Leidenschaft? Dann solltest Du uns kennen lernen!\n\nWir bei BAGHUS suchen zum nächstmöglichen Zeitpunkt Verstärkung für unser fantastisches Team in München, Murnau oder Augsburg.\n\nDeine Aufgaben\n\n* In unserem Microsoft-Team bist Du für den reibungslosen Betrieb sowohl unserer On-Premise-Infrastrukturen (Windows-Server, Active Directory, Remote Desktop Services) als auch für die Cloud-Services (Entra ID, Exchange Online, Teams, SharePoint Online, OneDrive, Cloud-To-Cloud-Backup) zuständig.\n* Du arbeitest an Projekten zur Einführung von Microsoft 365 Core-Services bei unseren Kunden mit und begleitest Migrationsprojekte\n* Du identifizierst die Anforderungen unserer Kunden, erarbeitest entsprechende Lösungen und setzt diese um Ggf. unterstützt Du unseren Vertrieb bei Presales-Terminen\n\nGut zu wissen:\n\nDas Windows-Serverteam erledigt seine Arbeiten während der Servicezeiten (8-17 Uhr) remote für die Kunden, wir haben keine Bereitschaften. Außerdem leisten wir nur 2nd- bzw. 3rd-Level Support für unsere Kunden.\n\n* Du hast eine erfolgreich abgeschlossene Ausbildung im IT-Bereich, ein entsprechendes Studium oder eine vergleichbare Qualifikation – oder bist Quereinsteiger mit entsprechender Erfahrung.\n* Du verfügst über sehr gute IT-Kenntnisse, insbesondere im Bereich der Microsoft-Server-Administration Kenntnisse in der Microsoft 365-Produktpalette, insbesondere Azure, SharePoint, Teams, Exchange und OneDrive sind von Vorteil\n* Erfahrung in der Planung und Durchführung von M365-Migrationsprojekten würden uns sehr freuen Zuverlässigkeit, Flexibilität und Freude an der Arbeit im Team zeichnen Dich aus\n* Sehr gute Deutsch- und Englischkenntnisse in Wort und Schrift runden Dein Profil ab\n\nBAGHUS bietet dir:\n\n* Unbefristete Arbeitsverträge\n* Regelmäßige Weiterbildung\n* Zentrale Büros\n* Flache Hierarchien & kurze Entscheidungswege\n* Job-Rad & Egym Wellpass\n* Firmenwagen & Jobticket\n* Betriebliche Altersvorsorge\n\nHört sich gut an? Dann freuen wir uns auf Deine Bewerbung!",
  "id": "https://www.linkedin.com/jobs/view/4383201063",
  "location": "Munich, Bavaria, Germany",
  "source": "LinkedIn",
  "title": "Administrator / Consultant Microsoft & M365 (m/w/d)",
  "url": "https://www.linkedin.com/jobs/view/4383201063",
  "sent_by_user": "Alexander Simon"
}
```

**Wrong output:** André Bittruf, Geschäftsführer (from Impressum, only 1 candidate found)
**Expected output:** Thomas, Teamleiter Windows & Infrastruktur (from team page)

---

## Case 3: Academia Gruppe — Treasury Manager (Finance)

The system picked **Marcus Geier (Geschäftsführer)** from the Impressum.
But on [academia-gruppe.de/ueber-uns](https://www.academia-gruppe.de/ueber-uns) there is a full leadership team including **Magali Guyot — Leadership Finanzen & Controlling**, which is the exact department for a Treasury Manager role.

**Test input:**
```json
{
  "category": "IT",
  "company": "Academia Gruppe",
  "contact_person": null,
  "date_posted": "2026-03-12",
  "description": "JOB BESCHREIBUNG\n\nDu suchst eine spannende Herausforderung in einer aufstrebenden Unternehmensgruppe in Deutschland? Du möchtest an unserem Ziel, Qualitätsführer im Spezialdiagnostikmarkt zu werden, mitarbeiten und mit uns neue Wege gehen? Dann starte jetzt gemeinsam mit uns durch! Wir sind eine dynamische Unternehmensgruppe im Gesundheitswesen mit rund 50 medizinischen Standorten deutschlandweit.\n\nFür unser Büro im Herzen Münchens suchen wir zum nächstmöglichen Zeitpunkt einen\nTreasury Manager (m/w/d)\nin Vollzeit.\n\nUnsere Benefits\n* Agilität. Flache Hierarchien und kurze Entscheidungswege ermöglichen es uns, gemeinsam Veränderung zu gestalten und Fortschritt voranzutreiben.\n* Flexibilität. Flexible Arbeitszeiten, die Möglichkeit zum Homeoffice sowie 30 Tage Urlaub pro Jahr unterstützen deine Work-Life-Balance.\n* Benefits. Mit Lunchit genießt du täglich ein leckeres Mittagessen. Zusätzlich erhältst du eine Mastercard, die wir monatlich mit 50 € bezuschussen, oder alternativ den EGYM Wellpass. Darüber hinaus fördern wir deine Weiterentwicklung mit einem Weiterbildungsbudget von bis zu 2.500 € pro Jahr und empfehlen LOTARO für deine persönliche Entwicklung.\n* Erstklassige Lage. Unser modernes Büro im Herzen Münchens bietet besten Kaffee, kostenlose Getränke und eine Auswahl an Snacks – offen für deine Wünsche.\n* Together We Rise. Gemeinsame Skitage, Wanderausflüge, Office-Yoga, Kochen oder Afterwork-Abende stärken unseren Teamgeist.\n* Willkommen im Team. Ein strukturiertes Onboarding sorgt für einen optimalen Start. Gleichzeitig erhältst du den Freiraum, eigenverantwortlich und agil zu arbeiten und deine Ideen einzubringen.\n\nVERANTWORTUNGSBEREICHE\n* Zentralisierung und Optimierung des Liquiditätsmanagements und Implementierung eines Cashpools für die gesamte Unternehmensgruppe\n* Steuerung und Überwachung der Liquidität auf Gruppenebene, inklusive detaillierter Liquiditätsplanung, Cashflow-Analysen und Prognosen\n* Projektverantwortung für die Einführung eines Treasury-Systems und Harmonisierung der Bankverbindungen\n* Verwaltung und Strukturierung der Bankkontenlandschaft und Integration in den Cashpool\n* Schnittstelle zu Standorten, Holding und Banken in allen Treasury-relevanten Fragestellungen, inkl. internationaler Ausrichtung\n* Entwicklung und Optimierung von Prozessen und Richtlinien im Cash Management, eigenverantwortliche Entscheidungen in enger Zusammenarbeit mit der Director of Finance\n\nKOMPETENZEN UND ANFORDERUNGEN\n* Abgeschlossenes betriebswirtschaftliches Studium mit Schwerpunkt Finance oder vergleichbare Qualifikation, idealerweise ergänzt durch eine Bankausbildung\n* Mehrjährige Berufserfahrung (3-5 Jahre) im Treasury und Cash Management, vorzugsweise in einem Konzernumfeld mit hoher Verantwortung\n* Erfahrung in der Einführung und Nutzung von Treasury-Management-Systemen (TMS) sowie Affinität zu IT und Projektmanagement\n* Ausgeprägtes Zahlenverständnis, analytisches Denken und strukturierte Arbeitsweise\n* Hohe Selbstständigkeit, Eigeninitiative und Kommunikationsstärke, kombiniert mit lösungsorientiertem Denken und der Fähigkeit, innovative Ideen umzusetzen\n* Verhandlungssichere Deutsch- und gute Englischkenntnisse sowie soziale Kompetenz für die Zusammenarbeit mit verschiedenen Teams und Standorten\n\nBei Fragen Stehen Wir Gerne Zur Verfügung Unter\nDann schreib uns eine Mail an karriere@academia-gruppe.de.\n\nWir freuen uns auf dich!",
  "id": "https://www.linkedin.com/jobs/view/4358734778",
  "location": "Munich, Bavaria, Germany",
  "source": "LinkedIn",
  "title": "Treasury Manager (m/w/d)",
  "url": "https://www.linkedin.com/jobs/view/4358734778",
  "sent_by_user": "Alexander Simon"
}
```

**Wrong output:** Marcus Geier, Geschäftsführer (from Impressum — 4 candidates found but still picked CEO)
**Expected output:** Magali Guyot, Leadership — Finanzen & Controlling (from team page)

---

## What to Verify

For each case, check:
1. **Team page discovery** — Does the system find the team/about page (`/ueber-moresophy`, `/ueber-uns`, etc.)?
2. **Candidate extraction** — Are people from the team page extracted as candidates (not just from Impressum)?
3. **Job-category matching** — Is the candidate whose role matches the job category ranked #1?
4. **Fallback logic** — CEO from Impressum is an acceptable fallback only when no better match exists on the team page.
