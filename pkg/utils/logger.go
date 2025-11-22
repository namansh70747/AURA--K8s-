package utils

import (
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/sirupsen/logrus"
	"gopkg.in/natefinch/lumberjack.v2"
)

var Log = logrus.New()

func init() {
	// Configure log formatter
	Log.SetFormatter(&logrus.JSONFormatter{
		TimestampFormat: "2006-01-02T15:04:05.000Z07:00",
	})

	// Configure log level with validation
	level := strings.ToLower(os.Getenv("LOG_LEVEL"))
	validLevels := map[string]logrus.Level{
		"debug": logrus.DebugLevel,
		"info":  logrus.InfoLevel,
		"warn":  logrus.WarnLevel,
		"error": logrus.ErrorLevel,
		"trace": logrus.TraceLevel,
		"fatal": logrus.FatalLevel,
		"panic": logrus.PanicLevel,
	}
	
	if levelStr, ok := validLevels[level]; ok {
		Log.SetLevel(levelStr)
	} else {
		// Default to info level if invalid
		Log.SetLevel(logrus.InfoLevel)
		if level != "" {
			Log.Warnf("Invalid LOG_LEVEL '%s', using default 'info'", level)
		}
	}

	// Configure multiple output destinations
	var writers []io.Writer
	writers = append(writers, os.Stdout) // Always log to stdout

	// Configure log file rotation if enabled
	logFile := os.Getenv("LOG_FILE")
	if logFile != "" {
		// Ensure log directory exists
		logDir := filepath.Dir(logFile)
		if logDir != "." && logDir != "" {
			if err := os.MkdirAll(logDir, 0755); err != nil {
				Log.WithError(err).Warnf("Failed to create log directory %s, using stdout only", logDir)
			} else {
				// Configure log rotation
				maxSize := 100 // MB
				if maxSizeStr := os.Getenv("LOG_MAX_SIZE_MB"); maxSizeStr != "" {
					if size, err := strconv.Atoi(maxSizeStr); err == nil && size > 0 {
						maxSize = size
					}
				}
				
				maxBackups := 3
				if backupsStr := os.Getenv("LOG_MAX_BACKUPS"); backupsStr != "" {
					if backups, err := strconv.Atoi(backupsStr); err == nil && backups > 0 {
						maxBackups = backups
					}
				}
				
				maxAge := 28 // days
				if ageStr := os.Getenv("LOG_MAX_AGE_DAYS"); ageStr != "" {
					if age, err := strconv.Atoi(ageStr); err == nil && age > 0 {
						maxAge = age
					}
				}
				
				fileWriter := &lumberjack.Logger{
					Filename:   logFile,
					MaxSize:    maxSize,
					MaxBackups: maxBackups,
					MaxAge:     maxAge,
					Compress:   os.Getenv("LOG_COMPRESS") == "true",
				}
				writers = append(writers, fileWriter)
				Log.Infof("Logging to file: %s (max size: %dMB, backups: %d, age: %d days)", 
					logFile, maxSize, maxBackups, maxAge)
			}
		}
	}

	// Set multi-writer output
	if len(writers) > 1 {
		Log.SetOutput(io.MultiWriter(writers...))
	} else {
		Log.SetOutput(os.Stdout)
	}
}
