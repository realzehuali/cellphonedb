import os
import io
import json
import sys
import time
import traceback

import pandas as pd

import boto3
import pika

from cellphonedb.app import app_config
from cellphonedb.core.CellphonedbSqlalchemy import CellphonedbSqlalchemy
from cellphonedb.app.app_logger import app_logger

try:
    s3_access_key = os.environ['S3_ACCESS_KEY']
    s3_secret_key = os.environ['S3_SECRET_KEY']
    s3_bucket_name = os.environ['S3_BUCKET_NAME']
    s3_endpoint = os.environ['S3_ENDPOINT']
    rabbit_host = os.environ['RABBIT_HOST']
    rabbit_port = os.environ['RABBIT_PORT']
    rabbit_user = os.environ['RABBIT_USER']
    rabbit_password = os.environ['RABBIT_PASSWORD']
    jobs_queue_name = os.environ['RABBIT_JOB_QUEUE']
    result_queue_name = os.environ['RABBIT_RESULT_QUEUE']


except KeyError as e:
    app_logger.error('ENVIRONMENT VARIABLE {} not defined. Please set it'.format(e))
    exit(1)


def create_rabbit_connection():
    return pika.BlockingConnection(pika.ConnectionParameters(
        host=rabbit_host,
        port=rabbit_port,
        virtual_host='/',
        credentials=credentials
    ))


config = app_config.AppConfig()
app = CellphonedbSqlalchemy(config.get_cellphone_core_config())

s3_resource = boto3.resource('s3', aws_access_key_id=s3_access_key,
                             aws_secret_access_key=s3_secret_key,
                             endpoint_url=s3_endpoint)
s3_client = boto3.client('s3', aws_access_key_id=s3_access_key,
                         aws_secret_access_key=s3_secret_key,
                         endpoint_url=s3_endpoint)


def read_data_from_s3(filename: str, s3_bucket_name: str):
    object = s3_client.get_object(Bucket=s3_bucket_name, Key=filename)
    bytestream = io.BytesIO(object['Body'].read())
    return pd.read_csv(bytestream, sep='\t', encoding='utf-8', index_col=0)


def write_data_in_s3(data: pd.DataFrame, filename: str):
    result_buffer = io.StringIO()
    data.to_csv(result_buffer, index=False, sep='\t')
    result_buffer.seek(0)

    # TODO: Find more elegant solution
    s3_client = boto3.client('s3', aws_access_key_id=s3_access_key,
                             aws_secret_access_key=s3_secret_key,
                             endpoint_url=s3_endpoint)

    encoding = result_buffer
    s3_client.put_object(Body=encoding.getvalue().encode('utf-8'), Bucket=s3_bucket_name, Key=filename)


def process_job(method, properties, body) -> dict:
    metadata = json.loads(body.decode('utf-8'))
    job_id = metadata['job_id']
    app_logger.info('New Job Queued: {}'.format(job_id))
    meta = read_data_from_s3(metadata['file_meta'], s3_bucket_name)
    counts = read_data_from_s3(metadata['file_counts'], s3_bucket_name)
    counts = counts.astype(dtype=pd.np.float64, copy=False)

    pvalues, means, significant_means, means_pvalues, deconvoluted = \
        app.method.cluster_statistical_analysis_launcher(meta,
                                                         counts,
                                                         threshold=float(metadata['threshold'] / 100),
                                                         iterations=int(metadata['iterations']),
                                                         debug_seed=-1,
                                                         threads=4)

    response = {
        'job_id': job_id,
        'files': {
            'pvalues': 'pvalues_simple_{}.txt'.format(job_id),
            'means': 'means_simple_{}.txt'.format(job_id),
            'significant_means': 'significant_means_simple_{}.txt'.format(job_id),
            'means_pvalues': 'means_pvalues_simple_{}.txt'.format(job_id),
            'deconvoluted': 'deconvoluted_simple_{}.txt'.format(job_id),
        },
        'success': True
    }

    write_data_in_s3(pvalues, response['files']['pvalues'])
    write_data_in_s3(means, response['files']['means'])
    write_data_in_s3(significant_means, response['files']['significant_means'])
    write_data_in_s3(means_pvalues, response['files']['means_pvalues'])
    write_data_in_s3(deconvoluted, response['files']['deconvoluted'])

    return response


credentials = pika.PlainCredentials(rabbit_user, rabbit_password)
connection = create_rabbit_connection()
channel = connection.channel()
channel.basic_qos(prefetch_count=1)

jobs_runned = 0

while jobs_runned < 3:
    job = channel.basic_get(queue=jobs_queue_name, no_ack=True)

    if all(job):
        try:
            job_response = process_job(*job)
            # TODO: Find more elegant solution
            connection = create_rabbit_connection()
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)

            channel.basic_publish(exchange='', routing_key=result_queue_name, body=json.dumps(job_response))
            app_logger.info('JOB %s PROCESSED' % job_response['job_id'])
        except Exception as e:
            error_response = {
                'job_id': json.loads(job[2].decode('utf-8'))['job_id'],
                'success': False,
                'error': {
                    'id': 'unknown_error',
                    'message': ''
                }
            }
            print(traceback.print_exc(file=sys.stdout))
            app_logger.error('[-] ERROR DURING PROCESSING JOB %s' % error_response['job_id'])
            if connection.is_closed:
                connection = create_rabbit_connection()
                channel = connection.channel()
                channel.basic_qos(prefetch_count=1)
            channel.basic_publish(exchange='', routing_key=result_queue_name, body=json.dumps(error_response))
            app_logger.error(e)

        jobs_runned += 1

    else:
        app_logger.debug('Empty queue')

    time.sleep(1)
