import os
from datetime import datetime
from typing import Dict

import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']


def get_sheets_service():
    creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON')
    if not creds_json:
        raise RuntimeError('GOOGLE_CREDENTIALS_JSON not set')
    creds = service_account.Credentials.from_service_account_info(
        __import__('json').loads(creds_json), scopes=SCOPES
    )
    return build('sheets', 'v4', credentials=creds, cache_discovery=False)


async def sync_to_sheets_with_audit(tab_name: str, excel_path, spreadsheet_id: str) -> Dict:
    service = get_sheets_service()
    sheets = service.spreadsheets()

    df_new = pd.read_excel(excel_path).fillna('').astype(str)

    try:
        sheet_data = (
            sheets.values()
            .get(spreadsheetId=spreadsheet_id, range=f"'{tab_name}'!A:ZZ")
            .execute()
            .get('values', [])
        )
    except Exception:
        sheet_data = []

    if sheet_data:
        headers = sheet_data[0]
        df_existing = pd.DataFrame(sheet_data[1:], columns=headers)
    else:
        df_existing = pd.DataFrame(columns=df_new.columns)

    all_cols = list(dict.fromkeys(list(df_existing.columns) + list(df_new.columns)))
    for c in all_cols:
        if c not in df_existing.columns: df_existing[c] = ''
        if c not in df_new.columns: df_new[c] = ''
    df_existing = df_existing[all_cols].fillna('').astype(str)
    df_new = df_new[all_cols].fillna('').astype(str)

    key = 'Candidate ID' if 'Candidate ID' in df_new.columns else df_new.columns[0]

    merged = pd.merge(df_existing, df_new, on=key, how='outer', suffixes=('_old','_new'), indicator=True)

    right_only = merged[merged['_merge']=='right_only']
    new_cols = [c for c in merged.columns if c.endswith('_new')] + [key]
    if new_cols:
        new_rows = right_only[new_cols].rename(columns={c:c.replace('_new','') for c in new_cols if c.endswith('_new')})
    else:
        new_rows = pd.DataFrame(columns=df_new.columns)

    updated_rows = []
    audit_entries = []
    both = merged[merged['_merge']=='both']
    for _, row in both.iterrows():
        changed = []
        for c in all_cols:
            if c == key: continue
            old = str(row.get(f'{c}_old','')).strip()
            new = str(row.get(f'{c}_new','')).strip()
            if old != new:
                changed.append((c, old, new))
        if changed:
            upd = {key: row[key]}
            for c in all_cols:
                if c == key: continue
                upd[c] = str(row.get(f'{c}_new','')).strip()
            updated_rows.append(upd)
            for (c, o, n) in changed:
                audit_entries.append([
                    datetime.now().strftime('%Y-%m-%d %H:%M:%S'), tab_name, row[key], 'UPDATED', c, o, n
                ])

    if not sheet_data:
        sheets.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption='RAW',
            body={'values':[all_cols]}
        ).execute()

    if not new_rows.empty:
        sheets.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption='RAW',
            insertDataOption='INSERT_ROWS',
            body={'values': new_rows[all_cols].values.tolist()}
        ).execute()

    if updated_rows:
        df_upd = pd.DataFrame(updated_rows)
        for _, r in df_upd.iterrows():
            idx = df_existing[df_existing[key] == r[key]].index
            if not idx.empty:
                rownum = idx[0] + 2
                vals = [str(r.get(c,'')) for c in all_cols]
                sheets.values().update(
                    spreadsheetId=spreadsheet_id,
                    range=f"'{tab_name}'!A{rownum}",
                    valueInputOption='RAW',
                    body={'values':[vals]}
                ).execute()

    if audit_entries:
        try:
            sheets.values().append(
                spreadsheetId=spreadsheet_id,
                range="'Audit Log'!A1",
                valueInputOption='RAW',
                insertDataOption='INSERT_ROWS',
                body={'values': audit_entries}
            ).execute()
        except Exception:
            batch = {
                'requests':[{'addSheet': {'properties': {'title':'Audit Log'}}}]
            }
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=batch).execute()
            sheets.values().update(
                spreadsheetId=spreadsheet_id,
                range="'Audit Log'!A1",
                valueInputOption='RAW',
                body={
                    'values': [["Timestamp","Tab Name","Candidate ID","Action","Column","Old Value","New Value"]] + audit_entries
                }
            ).execute()

    skipped = len(df_existing) - len(updated_rows) if not df_existing.empty else 0
    return {
        'tab': tab_name,
        'new_rows': 0 if new_rows is None else len(new_rows),
        'updated_rows': len(updated_rows),
        'skipped': max(skipped, 0)
    }


