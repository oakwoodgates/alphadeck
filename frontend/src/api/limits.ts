// The API's request input limits (security hardening PR 3, docs/AUTH.md §Request limits), in characters, mirrored
// from backend/domain/text_types.py so a form stops the user at the limit before it is sent (`maxLength`). The API
// still checks every value itself and answers 422 naming the field. backend/tests/app/test_request_bounds_guard.py
// keeps these numbers equal to the backend's.
export const LIMITS = {
  line: 200, // a one-line label: a name, a link label, a role, a catalyst label, a display name
  prose: 2000, // a link descriptor, thesis fit, a kill criterion, a reason, a ratify note
  narrative: 20000, // the thesis narrative
  term: 200, // a search term or seed
  url: 2000, // a URL or source reference
  email: 254,
  passwordMax: 1024,
  search: 100, // the company search box
} as const;
