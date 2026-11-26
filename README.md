# X-Portal
X-Portal is a server-side rendered web application built using FastAPI and SQLModel. It acts as a centralized, secure hub for managing various domain-specific business modules.

## run with-> uvicorn app.main:app --reload

## python create_superadmin.py email@example.com.tr password1 

## Atlas
Classes, attributes and relationships defined at runtime (Atlas › Class builder, for Data Admins and
Superadmins). Atlas keeps its data in `atlas.db`, separate from `db.sqlite3`: stop the server and delete
it to reset Atlas.

API (session cookie, or `Authorization: Bearer <token>` from `POST /auth/token`):

```
GET                 /atlas/api/users                       portal users (id, username, name, active)
GET POST            /atlas/api/classes
GET PUT DELETE      /atlas/api/classes/{class}
GET POST            /atlas/api/classes/{class}/objects     ?q=&sort=-_id&page=&page_size=&expand=*&brief=true&field__op=value
GET                 /atlas/api/classes/{class}/objects.xlsx   (or .csv) the same objects as a file, same parameters
GET PATCH DELETE    /atlas/api/classes/{class}/objects/{id}
GET PUT DELETE      /atlas/api/classes/{class}/objects/{id}/files/{attribute}   ?view=thumb|preview&download=true
POST                /atlas/api/classes/{class}/import      file=<xlsx|csv>, mapping=, match=, dry_run=true|false  (admins)
GET                 /atlas/api/classes/{class}/import/template.xlsx                                         (admins)
```

List and matrix attributes hold numbers. A list is `[210, 212.5]`. A matrix has columns (set in the class
builder) and is a list of rows, a value or null per column: `[[0, 0], [0.1, 210]]`. Writes also take text:
values separated by tabs, semicolons or `, `, rows by line breaks or `|` (`0; 0 | 0,5; 210`). `brief=true`
gives `{"count": n}` in their place (what the grid uses). They filter with `isnull` only and sort by length.

User attributes (the Technician of a test result) hold a portal user's id. Atlas keeps no copy of the users: it
reads their names from the portal, for the grid, titles, search, sort and exports. Writes and filters take the id
or the username (`technician=ayse`); `contains` matches the name. The portal won't delete a user that Atlas
names: deactivate the account instead, and the name stays.

Import is for Data Admins and Superadmins; everyone else adds objects in the form. The first row of the sheet
names the columns, matched to fields by label or key (or `mapping`, a JSON list of field keys, `""` to skip a
column). A row with an ID changes that object; a row without one creates one, or with `match=<a unique
attribute>` changes the object holding the same value. An empty cell clears its field. Links are titles or
`#id`, separated by semicolons; a `Roles` column takes role names the same way. Images and files aren't
imported: upload them in the form. It only checks (`dry_run=true`, the default) until sent with
`dry_run=false`, and then imports every row or none. Up to 50,000 rows. A row can link by title to an object
a row above it made; a cell showing an Excel error (`#N/A`) or a CSV row with more cells than the header is
reported, not imported. The template is a file for adding objects: a "Read me first" sheet with numbered
steps and what goes in each column (with an example from Atlas), then the sheet to fill in, whose columns
keep what is typed (Text, so 007 and 1-2 stay as they are), offer choices and yes/no as drop-downs, accept
only dates in date columns, and show a note on each cell. When an attribute is called ID, Version or Roles,
exports and imports call the system column "ID (Atlas)" and so on.

Filter operators: eq (default), ne, gt, gte, lt, lte, contains, startswith, in, isnull, on.

Image and file attributes: upload with `PUT` as multipart form data (field `file`, optional `_version`).
Images: PNG, JPEG, GIF, WebP, TIFF, BMP. Files: PDF, Word, Excel, PowerPoint, OpenDocument, CSV.
Uploads are kept inside `atlas.db`; the size limit is `ATLAS_MAX_FILE_MB` (default 25).
Tests (throwaway databases): `venv\Scripts\python.exe tests\test_atlas.py`