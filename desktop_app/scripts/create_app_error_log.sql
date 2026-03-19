-- Optional: create table for desktop app runtime/loop/selenium error logging
-- If this table does not exist, errors are only logged to console.
USE [GearUp]
GO
IF OBJECT_ID('APP_ERROR_LOG','U') IS NULL
CREATE TABLE APP_ERROR_LOG (
  ID INT IDENTITY(1,1) PRIMARY KEY,
  CLIENT_PHNO NVARCHAR(50),
  ERROR_TYPE NVARCHAR(50),
  ERROR_TEXT NVARCHAR(500),
  CREATED_DT DATETIME DEFAULT GETDATE()
);
GO
