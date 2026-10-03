// Starts the self-hosted Swagger UI on /docs. Nothing is loaded from elsewhere.
window.ui = SwaggerUIBundle({
  url: "/openapi.json", dom_id: "#swagger-ui", deepLinking: true,
  persistAuthorization: true, tryItOutEnabled: true, docExpansion: "list",
  defaultModelsExpandDepth: -1, displayRequestDuration: true,
});
