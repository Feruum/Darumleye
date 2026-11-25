# OpenAPI Specification 3.1.1 — selected API contract sections

Source: https://github.com/OAI/OpenAPI-Specification/blob/3.1.1/versions/3.1.1.md
Official rendered specification: https://spec.openapis.org/oas/v3.1.1.html
License: Apache License, Version 2.0 (see OPENAPI-LICENSE.txt).
Provenance: selected sections (in-document anchor links redirected to the official full specification) from the 3.1.1 release, retrieved 2026-11-25. This is a public technical reference, not BCC policy; use only if applicable to the project's API contract. The excerpt is not the full standard.

### Operation Object

Describes a single API operation on a path.

##### Fixed Fields

| Field Name | Type | Description |
| ---- | :----: | ---- |
| <a name="operation-tags"></a>tags | [`string`] | A list of tags for API documentation control. Tags can be used for logical grouping of operations by resources or any other qualifier. |
| <a name="operation-summary"></a>summary | `string` | A short summary of what the operation does. |
| <a name="operation-description"></a>description | `string` | A verbose explanation of the operation behavior. [CommonMark syntax](https://spec.commonmark.org/) MAY be used for rich text representation. |
| <a name="operation-external-docs"></a>externalDocs | [External Documentation Object](https://spec.openapis.org/oas/v3.1.1.html#external-documentation-object) | Additional external documentation for this operation. |
| <a name="operation-id"></a>operationId | `string` | Unique string used to identify the operation. The id MUST be unique among all operations described in the API. The operationId value is **case-sensitive**. Tools and libraries MAY use the operationId to uniquely identify an operation, therefore, it is RECOMMENDED to follow common programming naming conventions. |
| <a name="operation-parameters"></a>parameters | [[Parameter Object](https://spec.openapis.org/oas/v3.1.1.html#parameter-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object)] | A list of parameters that are applicable for this operation. If a parameter is already defined at the [Path Item](https://spec.openapis.org/oas/v3.1.1.html#path-item-parameters), the new definition will override it but can never remove it. The list MUST NOT include duplicated parameters. A unique parameter is defined by a combination of a [name](https://spec.openapis.org/oas/v3.1.1.html#parameter-name) and [location](https://spec.openapis.org/oas/v3.1.1.html#parameter-in). The list can use the [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object) to link to parameters that are defined in the [OpenAPI Object's `components.parameters`](https://spec.openapis.org/oas/v3.1.1.html#components-parameters). |
| <a name="operation-request-body"></a>requestBody | [Request Body Object](https://spec.openapis.org/oas/v3.1.1.html#request-body-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object) | The request body applicable for this operation. The `requestBody` is fully supported in HTTP methods where the HTTP 1.1 specification [RFC7231](https://tools.ietf.org/html/rfc7231#section-4.3.1) has explicitly defined semantics for request bodies. In other cases where the HTTP spec is vague (such as [GET](https://tools.ietf.org/html/rfc7231#section-4.3.1), [HEAD](https://tools.ietf.org/html/rfc7231#section-4.3.2) and [DELETE](https://tools.ietf.org/html/rfc7231#section-4.3.5)), `requestBody` is permitted but does not have well-defined semantics and SHOULD be avoided if possible. |
| <a name="operation-responses"></a>responses | [Responses Object](https://spec.openapis.org/oas/v3.1.1.html#responses-object) | The list of possible responses as they are returned from executing this operation. |
| <a name="operation-callbacks"></a>callbacks | Map[`string`, [Callback Object](https://spec.openapis.org/oas/v3.1.1.html#callback-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object)] | A map of possible out-of band callbacks related to the parent operation. The key is a unique identifier for the Callback Object. Each value in the map is a [Callback Object](https://spec.openapis.org/oas/v3.1.1.html#callback-object) that describes a request that may be initiated by the API provider and the expected responses. |
| <a name="operation-deprecated"></a>deprecated | `boolean` | Declares this operation to be deprecated. Consumers SHOULD refrain from usage of the declared operation. Default value is `false`. |
| <a name="operation-security"></a>security | [[Security Requirement Object](https://spec.openapis.org/oas/v3.1.1.html#security-requirement-object)] | A declaration of which security mechanisms can be used for this operation. The list of values includes alternative Security Requirement Objects that can be used. Only one of the Security Requirement Objects need to be satisfied to authorize a request. To make security optional, an empty security requirement (`{}`) can be included in the array. This definition overrides any declared top-level [`security`](https://spec.openapis.org/oas/v3.1.1.html#oas-security). To remove a top-level security declaration, an empty array can be used. |
| <a name="operation-servers"></a>servers | [[Server Object](https://spec.openapis.org/oas/v3.1.1.html#server-object)] | An alternative `servers` array to service this operation. If a `servers` array is specified at the [Path Item Object](https://spec.openapis.org/oas/v3.1.1.html#path-item-servers) or [OpenAPI Object](https://spec.openapis.org/oas/v3.1.1.html#oas-servers) level, it will be overridden by this value. |

This object MAY be extended with [Specification Extensions](https://spec.openapis.org/oas/v3.1.1.html#specification-extensions).

##### Operation Object Example

```json
{
  "tags": ["pet"],
  "summary": "Updates a pet in the store with form data",
  "operationId": "updatePetWithForm",
  "parameters": [
    {
      "name": "petId",
      "in": "path",
      "description": "ID of pet that needs to be updated",
      "required": true,
      "schema": {
        "type": "string"
      }
    }
  ],
  "requestBody": {
    "content": {
      "application/x-www-form-urlencoded": {
        "schema": {
          "type": "object",
          "properties": {
            "name": {
              "description": "Updated name of the pet",
              "type": "string"
            },
            "status": {
              "description": "Updated status of the pet",
              "type": "string"
            }
          },
          "required": ["status"]
        }
      }
    }
  },
  "responses": {
    "200": {
      "description": "Pet updated.",
      "content": {
        "application/json": {},
        "application/xml": {}
      }
    },
    "405": {
      "description": "Method Not Allowed",
      "content": {
        "application/json": {},
        "application/xml": {}
      }
    }
  },
  "security": [
    {
      "petstore_auth": ["write:pets", "read:pets"]
    }
  ]
}
```

```yaml
tags:
  - pet
summary: Updates a pet in the store with form data
operationId: updatePetWithForm
parameters:
  - name: petId
    in: path
    description: ID of pet that needs to be updated
    required: true
    schema:
      type: string
requestBody:
  content:
    application/x-www-form-urlencoded:
      schema:
        type: object
        properties:
          name:
            description: Updated name of the pet
            type: string
          status:
            description: Updated status of the pet
            type: string
        required:
          - status
responses:
  '200':
    description: Pet updated.
    content:
      application/json: {}
      application/xml: {}
  '405':
    description: Method Not Allowed
    content:
      application/json: {}
      application/xml: {}
security:
  - petstore_auth:
      - write:pets
      - read:pets
```

#
### Responses Object

A container for the expected responses of an operation.
The container maps a HTTP response code to the expected response.

The documentation is not necessarily expected to cover all possible HTTP response codes because they may not be known in advance.
However, documentation is expected to cover a successful operation response and any known errors.

The `default` MAY be used as a default Response Object for all HTTP codes
that are not covered individually by the Responses Object.

The Responses Object MUST contain at least one response code, and if only one
response code is provided it SHOULD be the response for a successful operation
call.

##### Fixed Fields

| Field Name | Type | Description |
| ---- | :----: | ---- |
| <a name="responses-default"></a>default | [Response Object](https://spec.openapis.org/oas/v3.1.1.html#response-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object) | The documentation of responses other than the ones declared for specific HTTP response codes. Use this field to cover undeclared responses. |

##### Patterned Fields

| Field Pattern | Type | Description |
| ---- | :----: | ---- |
| <a name="responses-code"></a>[HTTP Status Code](https://spec.openapis.org/oas/v3.1.1.html#http-status-codes) | [Response Object](https://spec.openapis.org/oas/v3.1.1.html#response-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object) | Any [HTTP status code](https://spec.openapis.org/oas/v3.1.1.html#http-status-codes) can be used as the property name, but only one property per code, to describe the expected response for that HTTP status code. This field MUST be enclosed in quotation marks (for example, "200") for compatibility between JSON and YAML. To define a range of response codes, this field MAY contain the uppercase wildcard character `X`. For example, `2XX` represents all response codes between `200` and `299`. Only the following range definitions are allowed: `1XX`, `2XX`, `3XX`, `4XX`, and `5XX`. If a response is defined using an explicit code, the explicit code definition takes precedence over the range definition for that code. |

This object MAY be extended with [Specification Extensions](https://spec.openapis.org/oas/v3.1.1.html#specification-extensions).

##### Responses Object Example

A 200 response for a successful operation and a default response for others (implying an error):

```json
{
  "200": {
    "description": "a pet to be returned",
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/Pet"
        }
      }
    }
  },
  "default": {
    "description": "Unexpected error",
    "content": {
      "application/json": {
        "schema": {
          "$ref": "#/components/schemas/ErrorModel"
        }
      }
    }
  }
}
```

```yaml
'200':
  description: a pet to be returned
  content:
    application/json:
      schema:
        $ref: '#/components/schemas/Pet'
default:
  description: Unexpected error
  content:
    application/json:
      schema:
        $ref: '#/components/schemas/ErrorModel'
```

#### Response Object

Describes a single response from an API operation, including design-time, static
`links` to operations based on the response.

##### Fixed Fields

| Field Name | Type | Description |
| ---- | :----: | ---- |
| <a name="response-description"></a>description | `string` | **REQUIRED**. A description of the response. [CommonMark syntax](https://spec.commonmark.org/) MAY be used for rich text representation. |
| <a name="response-headers"></a>headers | Map[`string`, [Header Object](https://spec.openapis.org/oas/v3.1.1.html#header-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object)] | Maps a header name to its definition. [RFC7230](https://tools.ietf.org/html/rfc7230#section-3.2) states header names are case insensitive. If a response header is defined with the name `"Content-Type"`, it SHALL be ignored. |
| <a name="response-content"></a>content | Map[`string`, [Media Type Object](https://spec.openapis.org/oas/v3.1.1.html#media-type-object)] | A map containing descriptions of potential response payloads. The key is a media type or [media type range](https://tools.ietf.org/html/rfc7231#appendix-D) and the value describes it. For responses that match multiple keys, only the most specific key is applicable. e.g. `"text/plain"` overrides `"text/*"` |
| <a name="response-links"></a>links | Map[`string`, [Link Object](https://spec.openapis.org/oas/v3.1.1.html#link-object) \| [Reference Object](https://spec.openapis.org/oas/v3.1.1.html#reference-object)] | A map of operations links that can be followed from the response. The key of the map is a short name for the link, following the naming constraints of the names for [Component Objects](https://spec.openapis.org/oas/v3.1.1.html#components-object). |

This object MAY be extended with [Specification Extensions](https://spec.openapis.org/oas/v3.1.1.html#specification-extensions).

##### Response Object Examples

Response of an array of a complex type:

```json
{
  "description": "A complex object array response",
  "content": {
    "application/json": {
      "schema": {
        "type": "array",
        "items": {
          "$ref": "#/components/schemas/VeryComplexType"
        }
      }
    }
  }
}
```

```yaml
description: A complex object array response
content:
  application/json:
    schema:
      type: array
      items:
        $ref: '#/components/schemas/VeryComplexType'
```

Response with a string type:

```json
{
  "description": "A simple string response",
  "content": {
    "text/plain": {
      "schema": {
        "type": "string"
      }
    }
  }
}
```

```yaml
description: A simple string response
content:
  text/plain:
    schema:
      type: string
```

Plain text response with headers:

```json
{
  "description": "A simple string response",
  "content": {
    "text/plain": {
      "schema": {
        "type": "string"
      },
      "example": "whoa!"
    }
  },
  "headers": {
    "X-Rate-Limit-Limit": {
      "description": "The number of allowed requests in the current period",
      "schema": {
        "type": "integer"
      }
    },
    "X-Rate-Limit-Remaining": {
      "description": "The number of remaining requests in the current period",
      "schema": {
        "type": "integer"
      }
    },
    "X-Rate-Limit-Reset": {
      "description": "The number of seconds left in the current period",
      "schema": {
        "type": "integer"
      }
    }
  }
}
```

```yaml
description: A simple string response
content:
  text/plain:
    schema:
      type: string
    example: 'whoa!'
headers:
  X-Rate-Limit-Limit:
    description: The number of allowed requests in the current period
    schema:
      type: integer
  X-Rate-Limit-Remaining:
    description: The number of remaining requests in the current period
    schema:
      type: integer
  X-Rate-Limit-Reset:
    description: The number of seconds left in the current period
    schema:
      type: integer
```

Response with no return value:

```json
{
  "description": "object created"
}
```

```yaml
description: object created
```

#
