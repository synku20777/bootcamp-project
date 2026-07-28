-- Phase 1 of account setup: run as ACCOUNTADMIN.
-- ACCOUNTADMIN is required to bootstrap the resource monitor, warehouse,
-- database, and least-privilege project role.
--
-- Automated setup executes this file, grants COVID_PROJECT_ADMIN and
-- COVID_APP_ROLE to CURRENT_USER(), reconnects as COVID_PROJECT_ADMIN, and
-- then executes 00_project_objects.sql. Keeping USE ROLE out of this phase
-- prevents a clean-account role authorization failure.

USE ROLE ACCOUNTADMIN;

CREATE RESOURCE MONITOR IF NOT EXISTS COVID_PROJECT_MONITOR
WITH
    CREDIT_QUOTA = 5
    FREQUENCY = MONTHLY
    START_TIMESTAMP = IMMEDIATELY
    TRIGGERS
        ON 50 PERCENT DO NOTIFY
        ON 80 PERCENT DO SUSPEND
        ON 100 PERCENT DO SUSPEND_IMMEDIATE;

CREATE WAREHOUSE IF NOT EXISTS COVID_WH
WITH
    WAREHOUSE_SIZE = XSMALL
    AUTO_SUSPEND = 60
    AUTO_RESUME = TRUE
    INITIALLY_SUSPENDED = TRUE
    COMMENT = 'Warehouse for the COVID-19 Bootcamp project';

-- Run this even when COVID_WH already existed.
ALTER WAREHOUSE COVID_WH
SET RESOURCE_MONITOR = COVID_PROJECT_MONITOR;

CREATE DATABASE IF NOT EXISTS COVID_ANALYTICS
COMMENT = 'Database for the COVID-19 analytics project';

CREATE ROLE IF NOT EXISTS COVID_PROJECT_ADMIN
COMMENT = 'Owns and operates the COVID-19 analytics project objects';

CREATE ROLE IF NOT EXISTS COVID_APP_ROLE
COMMENT = 'Least-privilege runtime role for FastAPI';

-- Replace the placeholder and run this grant once for the API user:
-- GRANT ROLE COVID_APP_ROLE TO USER "YOUR_SNOWFLAKE_USERNAME";

-- Project administrators inherit runtime privileges for grant verification.
GRANT ROLE COVID_APP_ROLE TO ROLE COVID_PROJECT_ADMIN;

-- A user with SYSADMIN can inherit the project role through this hierarchy.
GRANT ROLE COVID_PROJECT_ADMIN TO ROLE SYSADMIN;

GRANT USAGE, OPERATE, MONITOR ON WAREHOUSE COVID_WH
TO ROLE COVID_PROJECT_ADMIN;

GRANT USAGE ON WAREHOUSE COVID_WH
TO ROLE COVID_APP_ROLE;

GRANT USAGE, CREATE SCHEMA ON DATABASE COVID_ANALYTICS
TO ROLE COVID_PROJECT_ADMIN;

GRANT USAGE ON DATABASE COVID_ANALYTICS
TO ROLE COVID_APP_ROLE;

-- Marketplace data is delivered through a Snowflake share. Consumer account
-- roles receive access to the imported database as one grant; ordinary
-- database/schema/table grants are not valid for provider-owned shared data.
GRANT IMPORTED PRIVILEGES
ON DATABASE COVID19_EPIDEMIOLOGICAL_DATA
TO ROLE COVID_PROJECT_ADMIN;

SHOW RESOURCE MONITORS LIKE 'COVID_PROJECT_MONITOR';

SHOW WAREHOUSES LIKE 'COVID_WH';

SHOW DATABASES LIKE 'COVID_ANALYTICS';

-- Use PYTHON_ACCOUNT_IDENTIFIER as SNOWFLAKE_ACCOUNT in .env.
SELECT
    CURRENT_ORGANIZATION_NAME() AS ORGANIZATION_NAME,
    CURRENT_ACCOUNT_NAME() AS ACCOUNT_NAME,
    CURRENT_ORGANIZATION_NAME()
        || '-'
        || CURRENT_ACCOUNT_NAME() AS PYTHON_ACCOUNT_IDENTIFIER,
    CURRENT_ACCOUNT() AS ACCOUNT_LOCATOR,
    CURRENT_REGION() AS REGION,
    CURRENT_USER() AS USER_NAME;

-- Manual execution only: grant both roles to the user returned above before
-- running 00_project_objects.sql. Automated setup performs these grants with
-- bound identifiers and does not require editing this file.
