CREATE TABLE beneficiary AS
SELECT 2008 AS bene_year, * FROM read_csv('DE1_0_2008_Beneficiary_Summary_File_Sample_1.csv', header=true, nullstr='', auto_detect=true)
UNION ALL
SELECT 2009 AS bene_year, * FROM read_csv('DE1_0_2009_Beneficiary_Summary_File_Sample_1.csv', header=true, nullstr='', auto_detect=true);

CREATE TABLE carrier AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Carrier_Claims_Sample_1A.csv', header=true, nullstr='', auto_detect=true)
UNION ALL
SELECT * FROM read_csv('DE1_0_2008_to_2010_Carrier_Claims_Sample_1B.csv', header=true, nullstr='', auto_detect=true);

CREATE TABLE inpatient AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Inpatient_Claims_Sample_1.csv', header=true, nullstr='', auto_detect=true);

CREATE TABLE outpatient AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Outpatient_Claims_Sample_1.csv', header=true, nullstr='', auto_detect=true);

CREATE TABLE pde AS
SELECT * FROM read_csv('DE1_0_2008_to_2010_Prescription_Drug_Events_Sample_1.csv', header=true, nullstr='', auto_detect=true);
