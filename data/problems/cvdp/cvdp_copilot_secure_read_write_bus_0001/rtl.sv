module secure_read_write_bus_interface #(
    parameter int p_configurable_key=170,
    parameter int p_data_width=8,
    parameter int p_addr_width=8
) (
    input logic i_capture_pulse, i_reset_bar,
    input logic [p_addr_width-1:0] i_addr,
    input logic [p_data_width-1:0] i_data_in,
    input logic [7:0] i_key_in,
    input logic i_read_write_enable,
    output logic [p_data_width-1:0] o_data_out,
    output logic o_error
);
    localparam int DEPTH=1<<p_addr_width;
    logic [p_data_width-1:0] memory [0:DEPTH-1];
    always_ff @(posedge i_capture_pulse or negedge i_reset_bar) begin
        if(!i_reset_bar) begin
            o_data_out<=0;o_error<=0;
            for(int i=0;i<DEPTH;i++)memory[i]<=0;
        end else begin
            o_data_out<=0;o_error<=(i_key_in!=p_configurable_key);
            if(i_key_in==p_configurable_key) begin
                if(i_read_write_enable)o_data_out<=memory[i_addr];
                else memory[i_addr]<=i_data_in;
            end
        end
    end
endmodule
