module fsm_linear_reg #(parameter int DATA_WIDTH = 16) (
    input logic clk, reset, start,
    input logic signed [DATA_WIDTH-1:0] x_in, w_in, b_in,
    output logic signed [2*DATA_WIDTH-1:0] result1,
    output logic signed [DATA_WIDTH:0] result2,
    output logic done
);
    typedef enum logic [1:0] {IDLE=0, COMPUTE=1, DONE=2} state_t;
    state_t state;
    logic signed [2*DATA_WIDTH-1:0] product, result1_buffer;
    logic signed [DATA_WIDTH:0] x_extended, b_extended, result2_buffer;
    logic done_buffer;
    assign product=x_in*w_in;
    assign x_extended={x_in[DATA_WIDTH-1],x_in};
    assign b_extended={b_in[DATA_WIDTH-1],b_in};
    always_ff @(posedge clk or posedge reset) begin
        if (reset) begin
            state<=IDLE; result1<=0; result2<=0; done<=0;
            result1_buffer<=0; result2_buffer<=0; done_buffer<=0;
        end else begin
            result1<=result1_buffer; result2<=result2_buffer; done<=done_buffer;
            case (state)
                IDLE: begin
                    state<=start ? COMPUTE : IDLE;
                    result1_buffer<=0; result2_buffer<=0; done_buffer<=0;
                end
                COMPUTE: begin
                    state<=DONE;
                    result1_buffer<=product >>> 1;
                    result2_buffer<=b_extended+(x_extended >>> 2);
                    done_buffer<=0;
                end
                DONE: begin state<=IDLE; done_buffer<=1; end
                default: begin
                    state<=IDLE; result1_buffer<=0; result2_buffer<=0; done_buffer<=0;
                end
            endcase
        end
    end
endmodule
