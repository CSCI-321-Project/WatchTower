import boto3
import jwt 
import hmac
import hashlib
import base64
from botocore.exceptions import ClientError
from django.conf import settings
from .db_service import *

class CognitoService:
    def __init__(self):
        self.client = boto3.client(
            'cognito-idp',
            region_name=settings.COGNITO_AWS_REGION
        )
        self.client_id = settings.COGNITO_APP_CLIENT_ID
        self.client_secret = settings.COGNITO_APP_CLIENT_SECRET
        print(settings.COGNITO_AWS_REGION)
    def get_secret_hash(self, username):
        message = username + self.client_id
        dig = hmac.new(
            str(self.client_secret).encode('utf-8'),
            msg=str(message).encode('utf-8'),
            digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()

    def register_user(self, username, password, email, phone_number=None):
        try:
            username_check = self.check_username_exists(username)
            if username_check['exists']:
                return username_check
                
            user_attributes = [
                {
                    'Name': 'email',
                    'Value': email
                }
                # Uncomment for testing
                # {
                #     'Name': 'email_verified',
                #     'Value': 'true'
                # }
            ]

            if phone_number:
                user_attributes.append({
                    'Name': 'phone_number',
                    'Value': phone_number
                })

            params = {
                'ClientId': self.client_id,
                'Username': username,
                'Password': password,
                'UserAttributes': user_attributes
            }

            if self.client_secret:
                params['SecretHash'] = self.get_secret_hash(username)

            response = self.client.sign_up(**params)
            
            # After successful Cognito registration, create database record
            db_service = DatabaseService()
            db_result = db_service.create_user(
                cognito_id=response['UserSub'],
                name=username,
                email=email
            )
            
            if db_result['status'] != 'SUCCESS':
                # Delete Cognito user if database operation fails
                delete_result = self.delete_cognito_user(username)
                return {
                    'status': 'ERROR',
                    'message': 'Operation cancelled due to a server error. Please try again.',
                    'details': f"Database error: {db_result['message']}"
                }

            return {
                'status': 'SUCCESS',
                'user_sub': response['UserSub'],
                'message': 'User registration successful'
            }

        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }

    def delete_cognito_user(self, username):
        """
        Deletes a user from Cognito in case of database operation failure
        
        Args:
            username (str): Username of the user to delete
            
        Returns:
            dict: Status of the deletion operation
        """
        try:
            self.client.admin_delete_user(
                UserPoolId=self.user_pool_id,
                Username=username
            )
            return {
                'status': 'SUCCESS',
                'message': 'User deleted successfully'
            }
        except self.client.exceptions.UserNotFoundException:
            return {
                'status': 'ERROR',
                'message': 'User not found'
            }
        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }

    def confirm_sign_up(self, username, confirmation_code):
        try:
            params = {
                'ClientId': self.client_id,
                'Username': username,
                'ConfirmationCode': confirmation_code
            }

            if hasattr(settings, 'COGNITO_APP_CLIENT_SECRET'):
                params['SecretHash'] = self.get_secret_hash(username)

            self.client.confirm_sign_up(**params)
            
            return {
                'status': 'SUCCESS',
                'message': 'Email verification successful'
            }

        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }
        
    def check_username_exists(self, username):
        try:
            params = {
                'UserPoolId': settings.COGNITO_USER_POOL_ID,
                'Filter': f'username = "{username}"',
            }
            
            response = self.client.list_users(**params)
            
            # If any users are found with this username
            if response.get('Users', []):
                return {
                    'status': 'ERROR',
                    'exists': True,
                    'message': 'Username already exists'
                }
            
            return {
                'status': 'SUCCESS',
                'exists': False,
                'message': 'Username is available'
            }

        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }
    def login_user(self, username, password):
        try:
            params = {
                'USERNAME': username,
                'PASSWORD': password,
            }
            
            if self.client_secret:
                params['SECRET_HASH'] = self.get_secret_hash(username)

            response = self.client.initiate_auth(
                AuthFlow='USER_PASSWORD_AUTH',
                AuthParameters=params,
                ClientId=self.client_id
            )
            return{
                'status': 'SUCCESS',
                'message': 'Login successful',
                'access_token': response['AuthenticationResult']['AccessToken'],
                'refresh_token': response['AuthenticationResult']['RefreshToken'],
                'id_token': response['AuthenticationResult']['IdToken']
            }
        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }

    def renew_tokens(self, refresh_token):
        """
        Renew access and ID tokens using a refresh token
        
        Args:
            refresh_token (str): The refresh token from previous authentication
            
        Returns:
            dict: New tokens or error message
        """
        try:
            # Decode the refresh token to get the username
            decoded_token = jwt.decode(refresh_token, options={"verify_signature": False})
            username = decoded_token.get('username')

            if not username:
                return {
                    'status': 'ERROR',
                    'message': 'Could not extract username from refresh token'
                }

            params = {
                'ClientId': self.client_id,
                'AuthFlow': 'REFRESH_TOKEN_AUTH',
                'AuthParameters': {
                    'REFRESH_TOKEN': refresh_token
                }
            }

            if self.client_secret:
                params['AuthParameters']['SECRET_HASH'] = self.get_secret_hash(username)

            response = self.client.initiate_auth(**params)
            
            if 'AuthenticationResult' in response:
                return {
                    'status': 'SUCCESS',
                    'message': 'Tokens renewed successfully',
                    'access_token': response['AuthenticationResult'].get('AccessToken'),
                    'id_token': response['AuthenticationResult'].get('IdToken'),
                    'expires_in': response['AuthenticationResult'].get('ExpiresIn', 3600)
                }
            
            return {
                'status': 'ERROR',
                'message': 'Failed to renew tokens'
            }

        except jwt.InvalidTokenError:
            return {
                'status': 'ERROR',
                'message': 'Invalid refresh token format'
            }
        except self.client.exceptions.NotAuthorizedException:
            return {
                'status': 'ERROR',
                'message': 'Refresh token has expired or is invalid'
            }
        except Exception as e:
            return {
                'status': 'ERROR',
                'message': str(e)
            }

    def logout_user(self, accessToken):
        '''
        Logs out the user from the app
        '''
        try:
            response = self.client.global_sign_out(
                AccessToken=accessToken
            )
            return {
                'status': 'SUCCESS',
                'message': 'Logout successful'
            }
        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }

    def initiate_password_reset(self, id_token):
        """
        Initiates the password reset process for a user
        
        Args:
            id_token (str): The ID token from authentication
            
        Returns:
            dict: Status of the password reset initiation
        """
        try:
            # Extract username from ID token
            decoded_token = jwt.decode(id_token, options={"verify_signature": False})
            username = decoded_token.get('cognito:username')

            if not username:
                return {
                    'status': 'ERROR',
                    'message': 'Could not extract username from ID token'
                }

            params = {
                'ClientId': self.client_id,
                'Username': username
            }

            if self.client_secret:
                params['SecretHash'] = self.get_secret_hash(username)

            self.client.forgot_password(**params)
            
            return {
                'status': 'SUCCESS',
                'message': 'Password reset code sent to your email'
            }

        except jwt.InvalidTokenError:
            return {
                'status': 'ERROR',
                'message': 'Invalid token format'
            }
        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }

    def confirm_password_reset(self, id_token, confirmation_code, new_password):
        """
        Confirms password reset with the code and new password
        
        Args:
            id_token (str): The ID token from authentication
            confirmation_code (str): The code sent to user's email
            new_password (str): The new password to set
            
        Returns:
            dict: Status of the password reset confirmation
        """
        try:
            # Extract username from ID token
            decoded_token = jwt.decode(id_token, options={"verify_signature": False})
            username = decoded_token.get('cognito:username')

            if not username:
                return {
                    'status': 'ERROR',
                    'message': 'Could not extract username from ID token'
                }

            params = {
                'ClientId': self.client_id,
                'Username': username,
                'ConfirmationCode': confirmation_code,
                'Password': new_password
            }

            if self.client_secret:
                params['SecretHash'] = self.get_secret_hash(username)

            self.client.confirm_forgot_password(**params)
            
            return {
                'status': 'SUCCESS',
                'message': 'Password has been reset successfully'
            }

        except jwt.InvalidTokenError:
            return {
                'status': 'ERROR',
                'message': 'Invalid token format'
            }
        except ClientError as e:
            return {
                'status': 'ERROR',
                'error_code': e.response['Error']['Code'],
                'message': e.response['Error']['Message']
            }