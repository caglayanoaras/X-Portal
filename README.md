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
GET POST            /atlas/api/classes
GET PUT DELETE      /atlas/api/classes/{class}
GET POST            /atlas/api/classes/{class}/objects     ?q=&sort=-_id&page=&page_size=&expand=*&field__op=value
GET PATCH DELETE    /atlas/api/classes/{class}/objects/{id}
```

Filter operators: eq (default), ne, gt, gte, lt, lte, contains, startswith, in, isnull, on.
Tests (throwaway databases): `venv\Scripts\python.exe tests\test_atlas.py`