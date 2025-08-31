# راهنمای کامل و جامع دستورات psql (SQL Shell)

این راهنما شامل متداول‌ترین و کاربردی‌ترین دستورات `psql` است که برای مدیریت و کار با دیتابیس PostgreSQL به آن‌ها نیاز پیدا می‌کنید.

---

### **۱. اتصال به دیتابیس**

برای شروع کار با `psql`، ابتدا باید به یک دیتابیس متصل شوید. این کار با دستور `psql` در ترمینال یا Command Prompt انجام می‌شود.

* **فرمت کلی دستور اتصال:**
    ```bash
    psql -d database_name -U user_name -h host_address -p port_number
    ```
    * `-d`: نام دیتابیس (database)
    * `-U`: نام کاربری (user)
    * `-h`: آدرس هاست (host)
    * `-p`: شماره پورت (port)

* **مثال کاربردی:**
    ```bash
    psql -d my_app_db -U admin -h localhost
    ```

---

### **۲. دستورات داخلی (Meta-Commands)**

این دستورات با `\` (backslash) شروع می‌شوند و برای مدیریت خود `psql` و نمایش اطلاعات کلی دیتابیس به کار می‌روند. این دستورات با `;` (semicolon) تمام **نمی‌شوند**.

#### **الف) نمایش اطلاعات**

* `\l` یا `\list`: نمایش لیست تمام دیتابیس‌های موجود در سرور.
* `\c database_name`: اتصال به یک دیتابیس دیگر بدون خروج از `psql`.
* `\dt`: نمایش لیست تمام جداول (tables) در دیتابیس فعلی.
* `\d table_name`: نمایش ساختار یک جدول خاص (شامل ستون‌ها، نوع داده‌ها، ایندکس‌ها، کلیدهای خارجی و ...).
* `\dn`: نمایش لیست تمام schema ها.
* `\df`: نمایش لیست تمام توابع (functions).
* `\dv`: نمایش لیست تمام view ها.
* `\du`: نمایش لیست تمام کاربران و نقش‌ها (users/roles).

#### **ب) تنظیمات و راهنما**

* `\?`: نمایش راهنمای کامل دستورات داخلی `psql`.
* `\h SQL_COMMAND`: نمایش راهنمای یک دستور SQL خاص (مثلاً: `\h SELECT`).
* `\timing`: فعال یا غیرفعال کردن نمایش زمان اجرای هر کوئری.
* `\x`: تغییر حالت نمایش نتایج کوئری بین حالت عادی و حالت "بسط داده شده" (Expanded Display) که برای دیدن رکوردهای با ستون‌های زیاد بسیار مفید است.
* `\e`: باز کردن کوئری قبلی در یک ویرایشگر متن پیش‌فرض سیستم (مثل vi یا nano) برای ویرایش و اجرای مجدد.
* `\i file_path.sql`: اجرای دستورات SQL از یک فایل مشخص.

#### **ج) خروج**

* `\q`: خروج از محیط `psql`.

---

### **۳. دستورات استاندارد SQL (SQL Commands)**

اینها دستورات استاندارد زبان SQL هستند که برای کار با داده‌ها و ساختار دیتابیس استفاده می‌شوند. **تمام این دستورات باید با `;` تمام شوند.**

#### **الف) مدیریت دیتابیس (DDL - Database)**

* **ایجاد دیتابیس:**
    ```sql
    CREATE DATABASE database_name;
    ```
* **حذف دیتابیس:**
    ```sql
    DROP DATABASE database_name;
    ```

#### **ب) مدیریت جداول (DDL - Table)**

* **ایجاد جدول:**
    ```sql
    CREATE TABLE table_name (
        column1_name data_type constraints,
        column2_name data_type,
        ...
    );
    ```
    * **مثال:**
        ```sql
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            username VARCHAR(50) UNIQUE NOT NULL,
            email VARCHAR(100) UNIQUE NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
        );
        ```
* **حذف جدول:**
    ```sql
    DROP TABLE table_name;
    ```
* **تغییر ساختار جدول:**
    ```sql
    -- اضافه کردن یک ستون جدید
    ALTER TABLE table_name ADD COLUMN new_column_name data_type;

    -- حذف یک ستون
    ALTER TABLE table_name DROP COLUMN column_name;

    -- تغییر نام یک ستون
    ALTER TABLE table_name RENAME COLUMN old_name TO new_name;

    -- تغییر نوع داده یک ستون
    ALTER TABLE table_name ALTER COLUMN column_name TYPE new_data_type;

    -- اضافه کردن یک محدودیت (constraint)
    ALTER TABLE table_name ADD CONSTRAINT constraint_name UNIQUE (column_name);
    ```

#### **ج) کار با داده‌ها (DML - CRUD)**

* **درج داده (INSERT):**
    ```sql
    INSERT INTO table_name (column1, column2) VALUES ('value1', 'value2');
    ```
* **خواندن داده (SELECT):**
    ```sql
    -- انتخاب تمام ستون‌ها از یک جدول
    SELECT * FROM table_name;

    -- انتخاب ستون‌های خاص
    SELECT column1, column2 FROM table_name;

    -- خواندن داده با شرط (فیلتر کردن)
    SELECT * FROM table_name WHERE condition;

    -- مثال ترکیبی
    SELECT id, username, email FROM users WHERE created_at > '2025-01-01' ORDER BY username ASC;
    ```
* **به‌روزرسانی داده (UPDATE):**
    ```sql
    UPDATE table_name SET column1 = 'new_value1', column2 = 'new_value2' WHERE condition;
    ```
    **هشدار:** همیشه در دستور `UPDATE` از `WHERE` استفاده کنید، در غیر این صورت تمام رکوردهای جدول تغییر خواهند کرد!

* **حذف داده (DELETE):**
    ```sql
    DELETE FROM table_name WHERE condition;
    ```
    **هشدار:** همیشه در دستور `DELETE` از `WHERE` استفاده کنید، مگر اینکه بخواهید تمام رکوردهای جدول را حذف کنید!

#### **د) مدیریت کاربران و دسترسی‌ها (DCL)**

* **ایجاد کاربر (Role):**
    ```sql
    CREATE USER new_user WITH PASSWORD 'a_strong_password';
    -- یا به صورت جامع‌تر:
    CREATE ROLE new_role WITH LOGIN PASSWORD 'a_strong_password';
    ```
* **دادن دسترسی به کاربر:**
    ```sql
    -- دادن تمام دسترسی‌ها روی یک جدول
    GRANT ALL PRIVILEGES ON table_name TO role_name;

    -- دادن دسترسی‌های مشخص (مثلاً فقط خواندن و نوشتن)
    GRANT SELECT, INSERT ON table_name TO role_name;

    -- دادن دسترسی برای استفاده از یک دیتابیس
    GRANT CONNECT ON DATABASE database_name TO role_name;
    ```
* **گرفتن دسترسی از کاربر:**
    ```sql
    REVOKE ALL PRIVILEGES ON table_name FROM role_name;
    ```
* **حذف کاربر:**
    ```sql
    DROP USER user_name;
    DROP ROLE role_name;
    ```

---

### **۴. کلیدهای میانبر مفید در محیط psql**

* `Ctrl + C`: لغو (Cancel) کردن کوئری که در حال اجرا است.
* `Ctrl + L`: پاک کردن کامل صفحه ترمینال.
* `کلید جهت بالا / پایین`: مرور تاریخچه (History) دستورات وارد شده.
* `Tab`: کامل کردن خودکار (Auto-complete) نام دستورات SQL، جداول، ستون‌ها و... . بسیار کاربردی!