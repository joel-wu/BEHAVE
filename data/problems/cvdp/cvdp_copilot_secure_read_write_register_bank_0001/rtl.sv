module secure_read_write_register_bank #(
    parameter int p_address_width=8,
    parameter int p_data_width=8,
    parameter int p_unlock_code_0=171,
    parameter int p_unlock_code_1=205
) (
    input logic i_capture_pulse, i_rst_n,
    input logic [p_address_width-1:0] i_addr,
    input logic [p_data_width-1:0] i_data_in,
    input logic i_read_write_enable,
    output logic [p_data_width-1:0] o_data_out
);
    localparam int DEPTH=1<<p_address_width;
    logic [p_data_width-1:0] memory [0:DEPTH-1];
    logic unlocked,armed;
    always_ff @(posedge i_capture_pulse or negedge i_rst_n) begin
        if(!i_rst_n) begin
            unlocked<=0;armed<=0;o_data_out<=0;
            for(int i=0;i<DEPTH;i++)memory[i]<=0;
        end else begin
            o_data_out<=0;
            armed<=(!i_read_write_enable && i_addr==0 && i_data_in==p_unlock_code_0);
            if(!i_read_write_enable && i_addr==0) begin
                if(i_data_in!=p_unlock_code_0)unlocked<=0;
            end else if(!i_read_write_enable && i_addr==1) begin
                if(i_data_in!=p_unlock_code_1)unlocked<=0;
                else if(armed)unlocked<=1;
            end else if(unlocked && i_addr>=2) begin
                if(i_read_write_enable)o_data_out<=memory[i_addr];
                else memory[i_addr]<=i_data_in;
            end
        end
    end
endmodule
