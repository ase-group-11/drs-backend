# Code Review — `feature/disaster-reporting`

Reviewed: 2026-02-03
Branch: `feature/disaster-reporting`

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 6 |
| High | 5 |
| Medium | 6 |
| Low | 2 |

---

## Critical

Issues that will cause runtime crashes or silently corrupt data if left unfixed.

---

### 1. `BaseRepository.get_by_id` is hardcoded to `disaster_id`

**File:** `app/repositories/base_repository.py:28`

```python
def get_by_id(self, id: int) -> Optional[ModelType]:
    return self.db.query(self.model).filter(
        self.model.disaster_id == id   # <-- hardcoded
    ).first()
```

This is a **generic** base class. `disaster_id` does not exist on every model (e.g. `User` uses `user_id`). Any call to `get_by_id` or `delete` on a non-Disaster model will crash with an `AttributeError`. The primary key column needs to be resolved dynamically.

**Fix:** Use SQLAlchemy's inspection to find the primary key at runtime:
```python
from sqlalchemy import inspect as sa_inspect

def get_by_id(self, id: int) -> Optional[ModelType]:
    pk_col = sa_inspect(self.model).mapper.primary_key[0]
    return self.db.query(self.model).filter(pk_col == id).first()
```

---

### 2. Mutable default argument in `create_disaster`

**File:** `app/repositories/disaster_repository.py:24`

```python
def create_disaster(self, ..., image_urls: List[str] = []) -> Disaster:
```

`[]` is created once at function definition time and shared across every call. If anything ever mutates it, every subsequent call without an explicit argument sees the mutated list.

**Fix:**
```python
def create_disaster(self, ..., image_urls: Optional[List[str]] = None) -> Disaster:
    if image_urls is None:
        image_urls = []
```

---

### 3. Mutable default on the SQLAlchemy model column

**File:** `app/models/disaster.py:78`

```python
image_urls: Mapped[list] = mapped_column(ARRAY(String), nullable=True, default=[])
```

Same class of bug as above. SQLAlchemy will reuse the same list object.

**Fix:** Pass the callable, not an instance:
```python
image_urls: Mapped[list] = mapped_column(ARRAY(String), nullable=True, default=list)
```

---

### 4. `len(disaster.image_urls)` crashes when `image_urls` is `None`

**File:** `app/api/v1/disasters.py:71`

```python
'has_images': len(disaster.image_urls) > 0
```

The column is defined as `nullable=True`. If the value in the database is `NULL`, this line throws a `TypeError: object of type 'NoneType' has no len()`.

**Fix:**
```python
'has_images': bool(disaster.image_urls)
```

---

### 5. `get_pending_disasters` severity sort is alphabetical, not logical

**File:** `app/repositories/disaster_repository.py:81`

```python
.order_by(
    Disaster.severity.desc(),   # intended: critical first
    Disaster.created_at.asc()
)
```

The `severity` column stores strings. `.desc()` sorts alphabetically descending, which gives: `medium → low → high → critical`. The intended priority order (`critical → high → medium → low`) is completely wrong.

**Fix:** Use a `CASE` expression to map severity to a numeric priority:
```python
from sqlalchemy import case

severity_order = case(
    (Disaster.severity == 'critical', 4),
    (Disaster.severity == 'high', 3),
    (Disaster.severity == 'medium', 2),
    (Disaster.severity == 'low', 1),
    else_=0
)

return self.db.query(Disaster).filter(
    Disaster.status == 'pending'
).order_by(severity_order.desc(), Disaster.created_at.asc()).all()
```

---

### 6. Alembic downgrade will crash

**File:** `alembic/versions/08043ae77acb_add_user_and_disaster_models_with_.py:84`

```python
op.drop_index('idx_disasters_location', table_name='disasters', postgresql_using='gist')
```

This index is **never created** in `upgrade()` (it is commented out on line 53). Running `alembic downgrade` will fail with an error that the index does not exist.

The `downgrade()` also tries to recreate `spatial_ref_sys` (lines 68–76), which is owned by the PostGIS extension and should not be touched.

**Fix:** Remove both the `drop_index('idx_disasters_location', ...)` line and the `create_table('spatial_ref_sys', ...)` block from `downgrade()`.

---

## High

Logic bugs, security gaps, or edge cases that will surface in production or under concurrent load.

---

### 7. Signup verify returns `201 Created` for existing users

**File:** `app/api/v1/auth.py:43` + `app/services/auth_service.py:80`

`verify_and_create` has an idempotent check: if the user already exists it returns them with a session token. The endpoint always responds with `HTTP_201_CREATED`, which is semantically wrong for an already-existing resource and can mislead clients.

**Fix:** Have `verify_and_create` return a flag (or a tuple) indicating whether the user was newly created, and return `200` or `201` accordingly.

---

### 8. `UserVerify.mobile_number` has no validation

**File:** `app/schemas/user_schemas.py:21`

```python
class UserVerify(BaseModel):
    mobile_number: str          # no validator
    otp_code: str = Field(...)
```

`UserCreate` validates the mobile number against a regex (`^\+91\d{10}$`), but `UserVerify` accepts any string. An attacker can send arbitrary input directly to the OTP-verification flow.

**Fix:** Apply the same `validate_mobile` validator to `UserVerify`, or have it inherit from a shared base.

---

### 9. Hardcoded database credentials in test config

**File:** `app/tests/conftest.py:11`

```python
SQLALCHEMY_TEST_DATABASE_URL = "postgresql://pgadmin:Ase4life!@localhost:5432/drs_backend_test"
```

Credentials are in plain text in source code and will be committed to version control.

**Fix:** Read from an environment variable:
```python
import os
SQLALCHEMY_TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://localhost:5432/drs_backend_test"
)
```

---

### 10. Race condition in user creation — unhandled `IntegrityError`

**File:** `app/services/auth_service.py:76–103`

Between the `existing_user` check (line 76) and `self.db.commit()` (line 102), a second concurrent request for the same mobile number can commit first. The second request then hits the `unique` constraint on `mobile_number` and raises an unhandled `sqlalchemy.exc.IntegrityError`, which surfaces as a `500 Internal Server Error`.

**Fix:** Wrap the create + commit in a try/except for `IntegrityError`, and if caught, query and return the existing user (with a session token), the same way the explicit existing-user path does.

---

### 11. No login endpoint — signup/verify doubles as login

**File:** `app/services/auth_service.py:80–97`

`initiate_registration` blocks any mobile number that already exists (`400 — User already exists`). But `verify_and_create` silently returns a session token for that same existing user if the OTP is somehow still valid. There is no dedicated `/auth/login` endpoint.

A user whose OTP has expired (the normal case) has no way to log back in. This is a missing flow, not just an edge case.

**Fix:** Add a `/auth/login` endpoint with its own OTP flow that does not reject existing users.

---

## Medium

Code-quality issues, deprecated APIs, and edge cases that will not immediately crash but will cause subtle failures or warnings.

---

### 12. `authorization.split()` breaks on extra whitespace

**File:** `app/dependencies.py:40`

```python
scheme, token = authorization.split()
```

If the header value contains more than one space (e.g. `"Bearer  token"`), `split()` returns three elements and the unpacking raises a `ValueError`, which is caught — but the error message says "invalid format" when the real problem is extra whitespace.

**Fix:**
```python
scheme, token = authorization.split(maxsplit=1)
```

---

### 13. `get_current_user` is `async` for no reason

**File:** `app/dependencies.py:16`

```python
async def get_current_user(...) -> User:
```

Nothing inside the function uses `await`. Declaring it `async` when it performs synchronous Redis and DB calls means the event loop is blocked. It should be a regular `def`.

---

### 14. `import secrets` duplicated inside two function paths

**File:** `app/services/auth_service.py:86` and `:109`

`import secrets` is written twice inside the body of `verify_and_create`. It should be a single import at the top of the file.

---

### 15. `datetime.utcnow()` is deprecated

**File:** `app/core/audit.py:57`

```python
'timestamp': datetime.utcnow().isoformat(),
```

`datetime.utcnow()` is deprecated as of Python 3.12 and returns a naive datetime (no timezone info), making it ambiguous.

**Fix:**
```python
from datetime import datetime, timezone
'timestamp': datetime.now(timezone.utc).isoformat(),
```

---

### 16. `import json` inside the function body

**File:** `app/core/audit.py:65`

```python
def log_event(...):
    ...
    import json
    audit_logger.info(json.dumps(event_data))
```

This import is re-executed on every call. Move it to the top of the file.

---

### 17. `max_items` is deprecated in Pydantic V2

**File:** `app/schemas/disaster_schemas.py:37`

```python
image_urls: Optional[List[str]] = Field(default=[], max_items=5)
```

`max_items` was a Pydantic V1 parameter. In V2 it is `max_length` for list fields. This will emit a deprecation warning and may be silently ignored depending on the Pydantic version.

**Fix:**
```python
image_urls: Optional[List[str]] = Field(default=[], max_length=5)
```

---

### 18. `test_db` session is closed twice

**File:** `app/tests/conftest.py:31–45`

`test_db` fixture closes the session in its own `finally` block. The `override_get_db` inside the `client` fixture also closes `test_db` in its `finally`. Closing an already-closed session can raise warnings or errors depending on the driver.

**Fix:** Remove `test_db.close()` from `override_get_db`; let the `test_db` fixture own the lifecycle.

---

## Low

Minor issues unlikely to cause bugs but worth addressing for correctness and hygiene.

---

### 19. Image URL validation only checks the protocol prefix

**File:** `app/schemas/disaster_schemas.py:69`

```python
if not url.startswith(("http://", "https://")):
    raise ValueError(...)
```

A value like `"https://"` (no host or path) passes validation. Consider using `urllib.parse.urlparse` to verify at least a `netloc` (hostname) is present. Additionally, allowing plain `http://` for image sources is a security concern — consider restricting to `https://` only.

---

### 20. CORS is fully open

**File:** `main.py:39`

```python
allow_origins=["*"],
```

This is acceptable in development but must be locked down to specific origins before production.

---

## Checklist Before Merging

- [ ] Fix `BaseRepository.get_by_id` primary key resolution (Critical 1)
- [ ] Replace mutable defaults `[]` with `None` / `list` (Critical 2, 3)
- [ ] Guard `image_urls` against `None` before `len()` (Critical 4)
- [ ] Fix severity sort order with a `CASE` expression (Critical 5)
- [ ] Clean up the Alembic downgrade function (Critical 6)
- [ ] Return correct HTTP status code for existing users (High 7)
- [ ] Add validation to `UserVerify.mobile_number` (High 8)
- [ ] Move test DB credentials to an env variable (High 9)
- [ ] Wrap user creation in `IntegrityError` handling (High 10)
- [ ] Add a `/auth/login` endpoint (High 11)
- [ ] Use `split(maxsplit=1)` for Authorization header parsing (Medium 12)
- [ ] Change `get_current_user` from `async def` to `def` (Medium 13)
- [ ] Move `import secrets` and `import json` to file tops (Medium 14, 16)
- [ ] Replace `datetime.utcnow()` with `datetime.now(timezone.utc)` (Medium 15)
- [ ] Replace `max_items` with `max_length` (Medium 17)
- [ ] Remove the duplicate `test_db.close()` (Medium 18)
- [ ] Restrict image URLs to `https://` only and validate hostname (Low 19)
- [ ] Lock down CORS origins before production (Low 20)
