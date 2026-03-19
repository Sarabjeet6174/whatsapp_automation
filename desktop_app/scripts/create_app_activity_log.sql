-- Optional: create table for desktop app activity logs
-- UI/runtime events are written here (open/start/pause/resume/stop/poll/message status).
USE [GearUp]
GO
IF OBJECT_ID('APP_ACTIVITY_LOG','U') IS NULL
CREATE TABLE APP_ACTIVITY_LOG (
  ID INT IDENTITY(1,1) PRIMARY KEY,
  CLIENT_PHNO NVARCHAR(50),
  EVENT_TYPE NVARCHAR(50),
  MESSAGE NVARCHAR(1000),
  SOURCE NVARCHAR(50),
  CREATED_DT DATETIME DEFAULT GETDATE()
);
GO

