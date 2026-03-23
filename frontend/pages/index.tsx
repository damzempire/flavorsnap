/* eslint-disable @typescript-eslint/no-explicit-any */
import { useState } from "react";
import Image from "next/image";
import { ErrorMessage } from "@/components/ErrorMessage";
import { ImageUpload } from "@/components/ImageUpload";
import { useTranslation } from "next-i18next";
import { serverSideTranslations } from "next-i18next/serverSideTranslations";
import type { GetStaticProps } from "next";
import Layout from "@/components/Layout";
import { errorHandler, ErrorType, ErrorInfo } from '@/lib/error-handler';

export default function Classify() {
  const { t } = useTranslation("common");
  const [image, setImage] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [classification, setClassification] = useState<any>(null);
  const [retryCount, setRetryCount] = useState(0);

  const handleImageSelect = (selectedFile: File, imageUrl: string) => {
    setError(null);
    setClassification(null);
    setFile(selectedFile);
    setImage(imageUrl);
    setRetryCount(0);
  };

  const handleClassify = async () => {
    if (!file) return;
    
    setLoading(true);
    setError(null);

    // Register retry callback
    const operationId = `classify-${Date.now()}`;
    errorHandler.registerRetryCallback(operationId, () => handleClassify());

    try {
      const formData = new FormData();
      formData.append('file', file);

      const response = await fetch('/api/upload', {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();
      
      if (!response.ok) {
        const errorInfo = errorHandler.handleApiResponse(response, result);
        if (errorInfo) {
          setError(errorInfo);
          return;
        }
      }

      setClassification(result);
      setRetryCount(0);
    } catch (err: any) {
      const errorInfo = errorHandler.handleNetworkError(err);
      setError(errorInfo);
    } finally {
      setLoading(false);
      errorHandler.unregisterRetryCallback(operationId);
    }
  };

  const handleRetry = async () => {
    if (retryCount >= (error?.maxRetries || 3)) {
      // Max retries reached, show different message
      return;
    }
    
    setRetryCount(prev => prev + 1);
    await handleClassify();
  };

  const handleDismiss = () => {
    setError(null);
    setRetryCount(0);
  };

  return (

      </div>
      {/* ------------------------------------ */}

      <div className="flex flex-col items-center justify-center px-4 sm:px-6 py-4 sm:py-6 text-center">
        <h1 className="text-2xl sm:text-3xl lg:text-4xl font-bold mb-2">{t("snap_your_food")} 🍛</h1>
        <p className="text-sm sm:text-base text-gray-500 mb-6 sm:mb-8">Upload a photo to see the magic</p>

        {error && (
          <ErrorMessage
            message={error.userMessage}
            onRetry={error.retryable ? handleRetry : undefined}
            onDismiss={handleDismiss}
            variant="inline"
          />
        )}

        <ImageUpload
          onImageSelect={handleImageSelect}
          loading={loading}
          disabled={loading}
        />
        
        {image && (
          <div className="mt-6 sm:mt-8 w-full max-w-lg text-center">
            <div className="relative mx-auto w-full max-w-sm sm:max-w-md">
              <img
                src={image}
                alt={t("preview_alt")}
                className="w-full h-auto rounded-xl shadow-md border-2 border-accent/20 object-cover"
              />
            </div>

            <button
              onClick={handleClassify}
              disabled={loading}
              className="mt-4 sm:mt-6 bg-accent hover:bg-accent/90 text-white px-6 sm:px-8 py-3 sm:py-3.5 rounded-full text-sm sm:text-base font-medium disabled:bg-gray-400 disabled:cursor-not-allowed transition-all transform active:scale-95 touch-manipulation"
            >
              {loading ? (
                <span className="flex items-center justify-center">
                  <svg className="animate-spin -ml-1 mr-2 h-4 w-4 sm:h-5 sm:w-5" fill="none" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  {t('classifying')}
                </span>
              ) : (
                t('classify_food')
              )}
            </button>

            {classification && (
              <div className="mt-6 sm:mt-8 p-4 sm:p-6 bg-white dark:bg-gray-800 border border-green-200 dark:border-green-900 rounded-2xl shadow-sm max-w-sm mx-auto">
                <h3 className="font-bold text-lg sm:text-xl text-green-600 mb-2 break-words">{classification.label || classification.food}</h3>
                <p className="text-sm text-gray-500">
                  Confidence: {((classification.confidence || 0) * 100).toFixed(2)}%
                </p>
                {classification.calories && (
                  <p className="text-sm text-gray-500 mt-1">
                    Calories: {classification.calories}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </Layout>
  );
}

export const getStaticProps: GetStaticProps = async ({ locale }) => ({
  props: {
    ...(await serverSideTranslations(locale ?? "en", ["common"])),
  },
});