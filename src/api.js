const API_ORIGIN =
  import.meta.env.VITE_API_ORIGIN ||
  `${window.location.protocol}//${window.location.hostname}:8080`

export const apiUrl = (path) => `${API_ORIGIN}${path}`
