package utils

import (
	"io"
	"os"
	"path/filepath"

	lumberjack "gopkg.in/natefinch/lumberjack.v2"
)

// SetupFileLogging configures file logging with rotation
// Returns cleanup function to close log file
func SetupFileLogging(logDir, serviceName string, maxSizeMB, maxAgeDays, maxBackups int, compress bool) func() {
	if logDir == "" {
		logDir = "logs"
	}

	// Create log directory if it doesn't exist
	if err := os.MkdirAll(logDir, 0755); err != nil {
		Log.WithError(err).Warn("Failed to create log directory, using stdout only")
		return func() {}
	}

	logFile := filepath.Join(logDir, serviceName+".log")

	// Setup log rotation using lumberjack
	logRotator := &lumberjack.Logger{
		Filename:   logFile,
		MaxSize:    maxSizeMB, // megabytes
		MaxAge:     maxAgeDays, // days
		MaxBackups: maxBackups,
		Compress:   compress,
		LocalTime:  true,
	}

	// Create multi-writer for both stdout and file
	multiWriter := io.MultiWriter(os.Stdout, logRotator)
	Log.SetOutput(multiWriter)

	Log.Infof("File logging enabled: %s (max: %dMB, max age: %d days, backups: %d)", logFile, maxSizeMB, maxAgeDays, maxBackups)

	// Return cleanup function
	return func() {
		// Lumberjack handles rotation automatically, no explicit close needed
	}
}

