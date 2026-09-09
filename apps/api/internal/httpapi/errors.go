package httpapi

import (
	"encoding/json"
	"net/http"
)

// ErrorCode is a stable, machine-readable identifier for a failure class. The
// UI branches on these; humans read Message. Codes never change meaning once
// shipped.
type ErrorCode string

const (
	CodeInvalidRequest  ErrorCode = "invalid_request"
	CodeNotFound        ErrorCode = "not_found"
	CodeUpstreamFailure ErrorCode = "upstream_failure"
	CodeDataUnavailable ErrorCode = "data_unavailable"
	CodeInternal        ErrorCode = "internal_error"
	CodeNotImplemented  ErrorCode = "not_implemented"
	CodeTimeout         ErrorCode = "timeout"
)

// ErrorBody is the single error shape every endpoint returns. A uniform shape
// is what lets the UI show an honest, specific failure instead of a generic
// "something went wrong" (requirements.md NFR5.6).
type ErrorBody struct {
	Code ErrorCode `json:"code"`
	// Message is user-facing: it must say what went wrong and, where
	// possible, what the user can do about it.
	Message string `json:"message"`
	// Details carries structured context (e.g. which validation rule failed,
	// which symbol had a data gap). Optional.
	Details any `json:"details,omitempty"`
	// RequestID lets a user quote one identifier when something looks wrong.
	RequestID string `json:"request_id,omitempty"`
}

type errorEnvelope struct {
	Error ErrorBody `json:"error"`
}

// WriteJSON writes v as JSON with the given status.
func WriteJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	if v == nil {
		return
	}
	// The status line is already committed at this point, so an encoding
	// failure can only be logged by the caller's middleware, not corrected.
	_ = json.NewEncoder(w).Encode(v)
}

// WriteError writes the standard error envelope.
func WriteError(w http.ResponseWriter, r *http.Request, status int, code ErrorCode, message string, details any) {
	WriteJSON(w, status, errorEnvelope{Error: ErrorBody{
		Code:      code,
		Message:   message,
		Details:   details,
		RequestID: requestIDFromRequest(r),
	}})
}
