"""Reusable spreadsheet evidence and verified student-identifier boundaries."""
import re

USN = re.compile(r"(?<![a-z0-9])[1-9][a-z]{2}\d{2}[a-z]{2}\d{3}(?![a-z0-9])", re.I)


def identifiers(text):
    return sorted({m.upper() for m in USN.findall(str(text))})


def header(value):
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def mentor_value(values):
    fields={"facultymentor","mentor","mentorname","facultymentorname","assignedmentor","projectmentor"}
    return next((str(value).strip() for key,value in values.items()
                 if header(key) in fields and str(value).strip()),None)


def units(sheets):
    """Join only explicit team keys within one workbook; never fuzzy-match names."""
    output = []
    for sheet in sheets:
        for number, row in enumerate(sheet["rows"], 2):
            team = next((str(v).strip() for k,v in row.items()
                         if header(k) in {"teamnumber", "teamno", "teamid"} and str(v).strip()), "")
            team = re.sub(r"\.0$", "", team).upper()
            ids = sorted({number for key,value in row.items()
                if re.fullmatch(r"(?:(?:student|member)\d*(?:name)?|teamlead|lead)?usns?\d*|student\d*(?:id|number)|roll(?:no|number)",header(key))
                for number in identifiers(value)})
            output.append(dict(page=sheet["page"], sheet=sheet["name"], row=number,
                values=row, team=team, student_numbers=ids))
    # Team numbers can repeat across sections. Join rows only through a shared
    # exact student identifier, inside the same explicit team/scope context.
    parents=list(range(len(output)))
    def root(i):
        while parents[i]!=i:
            parents[i]=parents[parents[i]]
            i=parents[i]
        return i
    anchors={}
    contexts=[]
    for i,unit in enumerate(output):
        values={header(k):str(v).strip().casefold() for k,v in unit["values"].items()}
        context=(unit["team"],*(values.get(k,"") for k in ("department","semester","section")))
        contexts.append(context)
        if not unit["team"]:
            continue
        for number in unit["student_numbers"]:
            key=(context,number)
            if key in anchors:
                parents[root(i)]=root(anchors[key])
            anchors[key]=i
    groups={}
    by_context={}
    for i,unit in enumerate(output):
        if unit["student_numbers"]:
            groups.setdefault(root(i),set()).update(unit["student_numbers"])
            by_context.setdefault(contexts[i],set()).add(root(i))
    for i,unit in enumerate(output):
        if unit["student_numbers"]:
            unit["student_numbers"]=sorted(groups[root(i)])
        elif unit["team"] and len(by_context.get(contexts[i],set()))==1:
            unit["student_numbers"]=sorted(groups[next(iter(by_context[contexts[i]]))])
        unit["text"] = f"Worksheet: {unit['sheet']} | Row: {unit['row']}\n" + " | ".join(
            f"{k}: {v}" for k,v in unit["values"].items())
    return output


def contains_student_records(parsed):
    if any(identifiers(text) for _,text in parsed["pages"]):
        return True
    return any(re.search(r"usn|student\d*(?:name|id|number)|roll(?:no|number)",header(key))
               for unit in parsed.get("document_units",[]) for key in unit["values"])


def identity_match(column, number, dialect):
    from sqlalchemy import cast, exists, func, select
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB
        return cast(column, JSONB).contains([number])
    values = func.json_each(column).table_valued("value").alias()
    return exists(select(1).select_from(values).where(values.c.value == number))
