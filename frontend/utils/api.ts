import { ApiErrorResponse } from "../types";

interface ApiResponse<T = any> {
  data?: T;
  error?: string;
  status: number;
  progress?: number; // Progress percentage (0-100)
}

interface ApiOptions extends RequestInit {
  retries?: number;
  retryDelay?: number;
}

class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
    public data?: ApiErrorResponse,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

const apiRequest = async <T = any>(
  url: string,
  options: ApiOptions = {},
  onProgress?: (progress: number) => void, // Progress callback
): Promise<ApiResponse<T>> => {
  const { retries = 3, retryDelay = 1000, ...fetchOptions } = options;

  let lastError: Error | null = null;

  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const isFormData = typeof FormData !== "undefined" && fetchOptions.body instanceof FormData;
      const defaultHeaders: Record<string, string> = isFormData ? {} : { "Content-Type": "application/json" };

      // Track upload progress for FormData
      if (isFormData && onProgress && fetchOptions.body instanceof FormData) {
        const xhr = new XMLHttpRequest();
        
        return new Promise((resolve, reject) => {
          xhr.open(fetchOptions.method || 'POST', url);
          
          // Set headers
          Object.entries(defaultHeaders).forEach(([key, value]) => {
            if (value) xhr.setRequestHeader(key, value);
          });
          
          // Progress tracking
          xhr.upload.addEventListener('progress', (e) => {
            if (e.lengthComputable && onProgress) {
              const progress = Math.round((e.loaded / e.total) * 100);
              onProgress(progress);
            }
          });
          
          xhr.onload = () => {
            if (xhr.status >= 200 && xhr.status < 300) {
              try {
                const data = JSON.parse(xhr.responseText);
                resolve({ data, status: xhr.status });
              } catch {
                resolve({ data: undefined, status: xhr.status });
              }
            } else {
              try {
                const data = JSON.parse(xhr.responseText);
                reject(new ApiError(data.error || `HTTP ${xhr.status}`, xhr.status, data));
              } catch {
                reject(new ApiError(`HTTP ${xhr.status}`, xhr.status));
              }
            }
          };
          
          xhr.onerror = () => reject(new ApiError('Network error', 0));
          
          // Send FormData directly
          xhr.send(fetchOptions.body as XMLHttpRequestBodyInit);
        });
      }

      const response = await fetch(url, {
        ...fetchOptions,
        headers: {
          ...defaultHeaders,
          ...(fetchOptions.headers as Record<string, string>),
        },
      });

      const data = await response.json().catch(() => null);

      if (!response.ok) {
        const errorMessage =
          (data as ApiErrorResponse)?.error || (data as ApiErrorResponse)?.message || `HTTP ${response.status}`;
        throw new ApiError(errorMessage, response.status, data);
      }

      return {
        data: data as T,
        status: response.status,
      };
    } catch (error) {
      lastError =
        error instanceof Error ? error : new Error("Unknown error occurred");

      // Don't retry on client errors (4xx) except for 429 (rate limit)
      if (
        lastError instanceof ApiError &&
        lastError.status >= 400 &&
        lastError.status < 500 &&
        lastError.status !== 429
      ) {
        break;
      }

      // If this is the last attempt, don't wait
      if (attempt < retries) {
        await sleep(retryDelay * Math.pow(2, attempt)); // Exponential backoff
      }
    }
  }

  return {
    error: lastError?.message || "Request failed",
    status: lastError instanceof ApiError ? lastError.status : 500,
  };
};

// API methods with error handling
export const api = {
  get: <T = any>(url: string, options?: ApiOptions) =>
    apiRequest<T>(url, { method: "GET", ...options }),

  post: <T = any>(url: string, data?: any, options?: ApiOptions, onProgress?: (progress: number) => void) =>
    apiRequest<T>(url, {
      method: "POST",
      body: (typeof FormData !== "undefined" && data instanceof FormData) ? data : (data ? JSON.stringify(data) : undefined),
      ...options,
    }, onProgress),

  put: <T = any>(url: string, data?: any, options?: ApiOptions, onProgress?: (progress: number) => void) =>
    apiRequest<T>(url, {
      method: "PUT",
      body: data ? JSON.stringify(data) : undefined,
      ...options,
    }, onProgress),

  delete: <T = any>(url: string, options?: ApiOptions) =>
    apiRequest<T>(url, { method: "DELETE", ...options }),
};

export { ApiError };
export type { ApiResponse };
