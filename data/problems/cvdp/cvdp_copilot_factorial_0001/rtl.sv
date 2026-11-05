module factorial (
    input logic clk, arst_n,
    input logic [4:0] num_in,
    input logic start,
    output logic busy, done,
    output logic [63:0] fact
);
    typedef enum logic [1:0] {IDLE, BUSY, DONE} state_t;
    state_t state;
    logic [4:0] remaining;
    logic [63:0] accumulator;
    wire [63:0] product=accumulator*64'(remaining);
    assign busy=state==BUSY;
    assign done=state==DONE;
    always_ff @(posedge clk or negedge arst_n) begin
        if (!arst_n) begin state<=IDLE; remaining<=0; accumulator<=0; fact<=0; end
        else case (state)
            IDLE: if (start) begin
                state<=BUSY; remaining<=num_in; accumulator<=1;
            end
            BUSY: if (remaining<=1) begin
                fact<=accumulator; state<=DONE;
            end else begin
                accumulator<=product; remaining<=remaining-1'b1;
            end
            DONE: state<=IDLE;
            default: state<=IDLE;
        endcase
    end
endmodule
