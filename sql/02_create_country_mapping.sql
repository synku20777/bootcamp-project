-- Normalize known source codes and explicitly classify locations that cannot
-- be matched to the current World Bank population extract.
-- Run each statement separately in a Snowsight SQL worksheet.

USE ROLE COVID_PROJECT_ADMIN;

USE WAREHOUSE COVID_WH;

CREATE TABLE IF NOT EXISTS COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING (
    SOURCE_COUNTRY_NAME VARCHAR NOT NULL,
    SOURCE_COUNTRY_CODE VARCHAR,
    NORMALIZED_COUNTRY_NAME VARCHAR NOT NULL,
    NORMALIZED_ISO2 VARCHAR,
    NORMALIZED_ISO3 VARCHAR,
    EXPECTED_POPULATION_MATCH BOOLEAN NOT NULL DEFAULT TRUE,
    MAPPING_NOTE VARCHAR,
    UPDATED_AT TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
);

-- MERGE makes this seed operation repeatable and preserves any additional
-- project-specific mappings already added to the table.
MERGE INTO COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING AS target
USING (
    SELECT
        column1::VARCHAR AS SOURCE_COUNTRY_NAME,
        column2::VARCHAR AS SOURCE_COUNTRY_CODE,
        column3::VARCHAR AS NORMALIZED_COUNTRY_NAME,
        column4::VARCHAR AS NORMALIZED_ISO2,
        column5::VARCHAR AS NORMALIZED_ISO3,
        column6::BOOLEAN AS EXPECTED_POPULATION_MATCH,
        column7::VARCHAR AS MAPPING_NOTE
    FROM VALUES
        (
            'Anguilla', 'AI', 'Anguilla', 'AI', 'AIA', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Bonaire, Sint Eustatius and Saba', 'BQ',
            'Bonaire, Sint Eustatius and Saba', 'BQ', 'BES', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Cases on an international conveyance Japan', NULL,
            'International conveyance (Japan)', NULL, NULL, FALSE,
            'Not a population-bearing country'
        ),
        (
            'Falkland Islands (Malvinas)', 'FK', 'Falkland Islands',
            'FK', 'FLK', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Greece', 'EL', 'Greece', 'GR', 'GRC', TRUE,
            'ECDC uses EL; ISO 3166 uses GR'
        ),
        (
            'Guernsey', 'GG', 'Guernsey', 'GG', 'GGY', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Holy See (Vatican City State)', 'VA', 'Vatican City',
            'VA', 'VAT', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Jersey', 'JE', 'Jersey', 'JE', 'JEY', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Montserrat', 'MS', 'Montserrat', 'MS', 'MSR', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Namibia', NULL, 'Namibia', 'NA', 'NAM', TRUE,
            'ECDC source omits the Namibia ISO code'
        ),
        (
            'Taiwan, Province of China', 'TW', 'Taiwan', 'TW', 'TWN', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'United_Kingdom', 'UK', 'United Kingdom', 'GB', 'GBR', TRUE,
            'Normalize ECDC name and code'
        ),
        (
            'Wallis and Futuna', 'WF', 'Wallis and Futuna',
            'WF', 'WLF', FALSE,
            'Not present in current World Bank extract'
        ),
        (
            'Western Sahara', 'EH', 'Western Sahara', 'EH', 'ESH', FALSE,
            'Not present in current World Bank extract'
        )
) AS source
ON target.SOURCE_COUNTRY_NAME = source.SOURCE_COUNTRY_NAME
AND EQUAL_NULL(target.SOURCE_COUNTRY_CODE, source.SOURCE_COUNTRY_CODE)
WHEN MATCHED THEN UPDATE SET
    target.NORMALIZED_COUNTRY_NAME = source.NORMALIZED_COUNTRY_NAME,
    target.NORMALIZED_ISO2 = source.NORMALIZED_ISO2,
    target.NORMALIZED_ISO3 = source.NORMALIZED_ISO3,
    target.EXPECTED_POPULATION_MATCH = source.EXPECTED_POPULATION_MATCH,
    target.MAPPING_NOTE = source.MAPPING_NOTE,
    target.UPDATED_AT = CURRENT_TIMESTAMP()
WHEN NOT MATCHED THEN INSERT (
    SOURCE_COUNTRY_NAME,
    SOURCE_COUNTRY_CODE,
    NORMALIZED_COUNTRY_NAME,
    NORMALIZED_ISO2,
    NORMALIZED_ISO3,
    EXPECTED_POPULATION_MATCH,
    MAPPING_NOTE
)
VALUES (
    source.SOURCE_COUNTRY_NAME,
    source.SOURCE_COUNTRY_CODE,
    source.NORMALIZED_COUNTRY_NAME,
    source.NORMALIZED_ISO2,
    source.NORMALIZED_ISO3,
    source.EXPECTED_POPULATION_MATCH,
    source.MAPPING_NOTE
);

SELECT *
FROM COVID_ANALYTICS.RAW.COUNTRY_CODE_MAPPING
ORDER BY SOURCE_COUNTRY_NAME, SOURCE_COUNTRY_CODE;
